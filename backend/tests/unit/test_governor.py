"""Регулятор нагрузки шлюза: пороги диска, решения про файлы, темп, подмена сигналов."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core import hostinfo
from app.core.config import settings
from app.gateway import governor, load
from app.gateway import mtproto_receive as receive
from app.gateway.governor import GB, MB, DiskLevel, MediaChoice

OK, TIGHT, PAUSED, CRITICAL = DiskLevel.OK, DiskLevel.TIGHT, DiskLevel.PAUSED, DiskLevel.CRITICAL


# ----------------------------------------------------------------- диск


@pytest.mark.parametrize(
    ("total_gb", "thresholds_gb"),
    [(77, (15, 10, 5)), (200, (20, 12, 6)), (1000, (100, 60, 30)), (40, (15, 10, 5))],
)
def test_disk_thresholds_are_share_with_absolute_floor(total_gb, thresholds_gb):
    assert tuple(round(t / GB) for t in governor.disk_thresholds(total_gb * GB)) == thresholds_gb


@pytest.mark.parametrize(
    ("free_gb", "expected"),
    [
        (61, OK),
        (15, OK),
        (14.9, TIGHT),
        (10, TIGHT),
        (9.9, PAUSED),
        (5, PAUSED),
        (4.9, CRITICAL),
        (0, CRITICAL),
    ],
)
def test_disk_level_on_77gb_disk(free_gb, expected):
    assert governor.disk_level_for(int(free_gb * GB), 77 * GB) == expected


# --------------------------------------------------------- файлы: решения


def _decide(kind, size_mb, *, live, policy="all", level=OK):
    return governor.decide_media(
        kind=kind, size=int(size_mb * MB), live=live, policy=policy, level=level
    )


def test_everything_downloads_when_disk_is_fine_and_policy_is_all():
    for kind, size in (("photo", 0.2), ("voice", 1), ("video", 20), ("document", 8)):
        assert _decide(kind, size, live=False).choice == MediaChoice.DOWNLOAD
        assert _decide(kind, size, live=True).choice == MediaChoice.DOWNLOAD


def test_policy_light_skips_video_and_big_files_from_history_only():
    assert _decide("video", 2, live=False, policy="light").choice == MediaChoice.SKIP
    assert _decide("document", 6, live=False, policy="light").choice == MediaChoice.SKIP
    assert _decide("photo", 0.3, live=False, policy="light").choice == MediaChoice.DOWNLOAD
    assert _decide("voice", 1, live=False, policy="light").choice == MediaChoice.DOWNLOAD
    # свежее сообщение вживую настройка не касается
    assert _decide("video", 15, live=True, policy="light").choice == MediaChoice.DOWNLOAD


def test_policy_minimal_keeps_only_photo_voice_sticker():
    for kind in ("photo", "voice", "sticker"):
        assert _decide(kind, 0.5, live=False, policy="minimal").choice == MediaChoice.DOWNLOAD
    for kind in ("video", "document", "audio", "animation", "video_note"):
        decision = _decide(kind, 0.5, live=False, policy="minimal")
        assert decision.choice == MediaChoice.SKIP and decision.reason == governor.REASON_POLICY
    assert _decide("voice", 7, live=False, policy="minimal").choice == MediaChoice.SKIP
    assert _decide("video", 15, live=True, policy="minimal").choice == MediaChoice.DOWNLOAD


def test_tight_disk_defers_heavy_history_but_not_light_or_live():
    heavy = _decide("video", 3, live=False, level=TIGHT)
    assert heavy.choice == MediaChoice.DEFER and heavy.reason == governor.REASON_DISK
    assert _decide("document", 8, live=False, level=TIGHT).choice == MediaChoice.DEFER
    assert _decide("photo", 0.2, live=False, level=TIGHT).choice == MediaChoice.DOWNLOAD
    assert _decide("video", 15, live=True, level=TIGHT).choice == MediaChoice.DOWNLOAD


def test_paused_disk_defers_all_history_files_and_keeps_live_ones():
    assert _decide("photo", 0.1, live=False, level=PAUSED).choice == MediaChoice.DEFER
    assert _decide("voice", 1, live=False, level=PAUSED).choice == MediaChoice.DEFER
    assert _decide("voice", 1, live=True, level=PAUSED).choice == MediaChoice.DOWNLOAD


def test_critical_disk_defers_everything_even_live():
    for live in (False, True):
        decision = _decide("photo", 0.1, live=live, level=CRITICAL)
        assert decision.choice == MediaChoice.DEFER and decision.reason == governor.REASON_DISK


def test_policy_skip_beats_disk_deferral():
    """Не скачиваем вовсе — нечего откладывать «до появления места»."""
    assert _decide("video", 9, live=False, policy="light", level=PAUSED).choice == MediaChoice.SKIP


# ------------------------------------------------------------ темп и очередь


def _signals(pressure=None, level=OK):
    return governor.Signals(
        disk_free=60 * GB, disk_total=77 * GB, disk_level=level, pressure=pressure, taken_at=0
    )


@pytest.mark.parametrize(
    ("pressure", "factor"),
    [(None, 1.0), (0.3, 1.0), (0.6, 1.0), (0.75, 2.5), (0.9, 4.0), (1.4, 4.0)],
)
def test_dialog_pause_grows_with_pressure(monkeypatch, pressure, factor):
    monkeypatch.setattr(governor, "signals", lambda **_: _signals(pressure))
    assert governor.dialog_pause(0.4) == pytest.approx(0.4 * factor)


def test_media_pacing_only_when_server_is_busy(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(governor.asyncio, "sleep", fake_sleep)
    for pressure, expected in ((None, []), (0.5, []), (0.7, [1.0]), (0.95, [4.0])):
        sleeps.clear()
        monkeypatch.setattr(governor, "signals", lambda p=pressure, **_: _signals(p))
        asyncio.run(governor.pace_media(20 * MB))
        assert sleeps == expected, (pressure, sleeps)


@pytest.mark.parametrize(
    ("level", "pressure", "expected"),
    [
        (OK, 0.3, None),
        (OK, None, None),
        (TIGHT, 0.3, governor.HEAVY_BYTES),
        (PAUSED, 0.3, 0),
        (CRITICAL, 0.3, 0),
        (OK, 0.95, 0),
    ],
)
def test_backfill_limit(monkeypatch, level, pressure, expected):
    monkeypatch.setattr(governor, "signals", lambda **_: _signals(pressure, level))
    assert governor.backfill_limit() == expected


# --------------------------------------------- нагрузка: память контейнера


@pytest.mark.parametrize(
    ("container", "expected_capacity"),
    [(None, 25), (0.5, 25), (0.70, 25), (0.79, 12), (0.95, 1)],
)
def test_container_memory_throttles_new_accounts(monkeypatch, container, expected_capacity):
    monkeypatch.setattr(hostinfo, "host_load_ratio", lambda: 0.1)
    monkeypatch.setattr(hostinfo, "host_memory_ratio", lambda: 0.2)
    monkeypatch.setattr(hostinfo, "container_memory_ratio", lambda: container)
    assert load.effective_capacity(25) == expected_capacity


def test_explicit_zero_capacity_is_respected(monkeypatch):
    monkeypatch.setattr(hostinfo, "host_load_ratio", lambda: 0.0)
    assert load.effective_capacity(0) == 0


# ------------------------------------------------------- подмена сигналов


def test_override_file_changes_disk_signal_outside_production(monkeypatch, tmp_path):
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"disk_free_gb": 3, "disk_total_gb": 77}))
    monkeypatch.setenv("ASTRA_PROBE_OVERRIDE_FILE", str(path))
    monkeypatch.setattr(settings, "app_env", "local")
    governor.reset_cache()
    try:
        current = governor.signals(force=True)
        assert current.disk_level == CRITICAL and current.disk_free == 3 * GB
    finally:
        governor.reset_cache()


def test_override_file_is_ignored_in_production(monkeypatch, tmp_path):
    path = tmp_path / "override.json"
    path.write_text(json.dumps({"disk_free_gb": 1, "disk_total_gb": 77}))
    monkeypatch.setenv("ASTRA_PROBE_OVERRIDE_FILE", str(path))
    monkeypatch.setattr(settings, "app_env", "production")
    governor.reset_cache()
    try:
        assert governor.signals(force=True).disk_free != 1 * GB
    finally:
        governor.reset_cache()


def test_broken_override_file_does_not_break_signals(monkeypatch, tmp_path):
    path = tmp_path / "override.json"
    path.write_text("{не json")
    monkeypatch.setenv("ASTRA_PROBE_OVERRIDE_FILE", str(path))
    monkeypatch.setattr(settings, "app_env", "local")
    governor.reset_cache()
    try:
        assert governor.signals(force=True).disk_total > 0
    finally:
        governor.reset_cache()


def test_metrics_line_is_readable():
    line = governor.metrics_line("host-abc", 7)
    assert "аккаунтов 7" in line and "диск свободно" in line and "host-abc" in line


# ------------------------------------- read_media: решения доходят до вложения


class _FakeClient:
    def __init__(self):
        self.downloads = 0

    async def download_media(self, message, file=bytes):
        self.downloads += 1
        return b"x" * 1000


def _read(monkeypatch, *, kind, size_mb, live, policy="all", level=OK):
    client = _FakeClient()
    message = SimpleNamespace(id=42, chat_id=777)
    monkeypatch.setattr(receive, "media_kind", lambda _m: kind)
    monkeypatch.setattr(
        receive,
        "_file_meta",
        lambda _m, k: {
            "kind": k,
            "size": int(size_mb * MB),
            "file_name": f"f.{k}",
            "mime_type": None,
        },
    )

    async def fake_policy():
        return policy

    monkeypatch.setattr(governor, "history_policy", fake_policy)
    monkeypatch.setattr(governor, "disk_level", lambda: level)
    monkeypatch.setattr(governor, "signals", lambda **_: _signals(None, level))
    kind_out, items = asyncio.run(receive.read_media(client, message, 6, live=live))
    return client, kind_out, items[0]


def test_read_media_downloads_normally(monkeypatch):
    client, _, item = _read(monkeypatch, kind="photo", size_mb=1, live=True)
    assert client.downloads == 1 and item["status"] == "ready" and item["body"]


def test_read_media_skips_by_policy_without_download(monkeypatch):
    client, _, item = _read(monkeypatch, kind="video", size_mb=3, live=False, policy="light")
    assert client.downloads == 0
    assert item["status"] == "too_large" and item["error"] == governor.REASON_POLICY


def test_read_media_defers_history_when_disk_is_paused(monkeypatch):
    client, _, item = _read(monkeypatch, kind="photo", size_mb=0.2, live=False, level=PAUSED)
    assert client.downloads == 0
    assert item["status"] == "pending" and item["paused"] == "disk"
    assert item["source"]["tg_message_id"] == 42 and item["error"] == governor.REASON_DISK


def test_read_media_keeps_downloading_live_files_when_disk_is_paused(monkeypatch):
    client, _, item = _read(monkeypatch, kind="photo", size_mb=0.2, live=True, level=PAUSED)
    assert client.downloads == 1 and item["status"] == "ready"


def test_read_media_defers_even_live_files_when_disk_is_critical(monkeypatch):
    client, _, item = _read(monkeypatch, kind="voice", size_mb=1, live=True, level=CRITICAL)
    assert client.downloads == 0 and item["status"] == "pending" and item["paused"] == "disk"


def test_read_media_big_file_goes_to_background_queue_as_before(monkeypatch):
    client, _, item = _read(monkeypatch, kind="document", size_mb=30, live=True)
    assert client.downloads == 0 and item["status"] == "pending" and "paused" not in item
