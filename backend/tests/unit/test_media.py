"""Медиа-модуль: разбор ffprobe, волна, диапазоны, настоящие перекодирования."""

import asyncio
import math
import os
import struct
import subprocess
import tempfile
import wave as wavelib

import pytest

from app.services import media
from app.services.file_serving import parse_range
from tests.unit.conftest import needs_ffmpeg

# --------------------------------------------------------------- без ffmpeg


def test_parse_probe_video_with_rotation():
    raw = {
        "format": {"format_name": "mov,mp4", "duration": "12.4", "tags": {}},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "side_data_list": [{"rotation": -90}],
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    info = media.parse_probe(raw)
    assert info.has_video and info.has_audio
    # Снято «стоя»: показывать надо 1080×1920, а не как записано.
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration_sec == 12


def test_parse_probe_ignores_album_cover():
    raw = {
        "format": {"duration": "200.0", "tags": {"title": "Песня", "artist": "Исполнитель"}},
        "streams": [
            {"codec_type": "audio", "codec_name": "mp3"},
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ],
    }
    info = media.parse_probe(raw)
    assert info.has_audio and not info.has_video
    assert info.title == "Песня" and info.performer == "Исполнитель"


def test_parse_probe_garbage_is_safe():
    info = media.parse_probe({"format": {"duration": "N/A"}, "streams": [{"codec_type": "video"}]})
    assert info.duration is None and info.width is None


def test_waveform_from_pcm_normalizes_to_31():
    samples = [int(10000 * math.sin(i / 20)) * (1 if i < 4000 else 0) for i in range(8000)]
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    values = media.waveform_from_pcm(pcm, points=100)
    assert len(values) == 100
    assert max(values) == 31
    # вторая половина — тишина
    assert all(value == 0 for value in values[55:])


def test_waveform_of_silence_is_flat_zero():
    assert media.waveform_from_pcm(b"\x00\x00" * 1000, points=10) == [0] * 10


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        (None, 100, None),
        ("bytes=0-9", 100, (0, 9)),
        ("bytes=90-", 100, (90, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=0-1000", 100, (0, 99)),
        ("bytes=0-1,5-6", 100, None),
        ("items=0-1", 100, None),
    ],
)
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize("header", ["bytes=100-", "bytes=5-2", "bytes=abc-", "bytes=-0"])
def test_parse_range_unsatisfiable(header):
    with pytest.raises(ValueError):
        parse_range(header, 100)


# ------------------------------------------------------------ с ffmpeg


def _write_tone_wav(path: str, seconds: float = 2.0) -> None:
    rate = 16000
    with wavelib.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            loud = 1.0 if i < rate * seconds / 2 else 0.2
            frames += struct.pack("<h", int(12000 * loud * math.sin(2 * math.pi * 440 * i / rate)))
        out.writeframes(bytes(frames))


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@needs_ffmpeg
def test_voice_from_browser_webm_becomes_telegram_ogg_opus():
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "tone.wav")
        _write_tone_wav(wav)
        # Так пишет Chrome: WebM с Opus.
        recording = os.path.join(tmp, "recording")
        _ffmpeg("-i", wav, "-c:a", "libopus", "-f", "webm", recording)
        target = os.path.join(tmp, "voice.ogg")

        async def scenario():
            await media.transcode_voice(recording, target)
            info = await media.probe(target)
            wave = await media.waveform(target)
            return info, wave

        info, wave = asyncio.run(scenario())
        assert info is not None and info.has_audio and not info.has_video
        assert info.audio_codec == "opus"
        assert "ogg" in info.format_name
        assert info.duration_sec == 2
        assert len(wave) == 100 and max(wave) == 31
        # громкая первая половина и тихая вторая видны на волне
        assert sum(wave[:40]) > sum(wave[60:]) * 2


@needs_ffmpeg
def test_voice_from_safari_mp4_aac_is_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "tone.wav")
        _write_tone_wav(wav, 1.5)
        recording = os.path.join(tmp, "recording")
        _ffmpeg("-i", wav, "-c:a", "aac", "-f", "mp4", recording)
        target = os.path.join(tmp, "voice.ogg")
        asyncio.run(media.transcode_voice(recording, target))
        info = asyncio.run(media.probe(target))
        assert info is not None and info.audio_codec == "opus"


@needs_ffmpeg
def test_video_probe_and_thumbnail():
    with tempfile.TemporaryDirectory() as tmp:
        video = os.path.join(tmp, "clip.mp4")
        _ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=640x360:rate=25:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            video,
        )
        thumb = os.path.join(tmp, "thumb.jpg")

        async def scenario():
            info = await media.probe(video)
            ok = await media.video_thumbnail(video, thumb, info.duration if info else None)
            return info, ok

        info, ok = asyncio.run(scenario())
        assert info is not None and info.has_video
        assert (info.width, info.height) == (640, 360)
        assert info.duration_sec == 2
        assert ok and os.path.getsize(thumb) > 0
        size = media.image_size(thumb)
        assert size is not None and max(size) <= media.THUMB_SIDE


@needs_ffmpeg
def test_ogg_to_m4a_for_safari():
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "tone.wav")
        _write_tone_wav(wav, 1.0)
        ogg = os.path.join(tmp, "voice.ogg")
        asyncio.run(media.transcode_voice(wav, ogg))
        m4a = os.path.join(tmp, "voice.m4a")
        asyncio.run(media.to_m4a(ogg, m4a))
        info = asyncio.run(media.probe(m4a))
        assert info is not None and info.audio_codec == "aac"


@needs_ffmpeg
def test_broken_file_is_not_media():
    with tempfile.TemporaryDirectory() as tmp:
        junk = os.path.join(tmp, "junk.mp4")
        with open(junk, "wb") as out:
            out.write(b"not a video at all" * 100)
        assert asyncio.run(media.probe(junk)) is None
        with pytest.raises(media.MediaError):
            asyncio.run(media.transcode_voice(junk, os.path.join(tmp, "out.ogg")))
