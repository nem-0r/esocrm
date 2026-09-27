"""Как сообщение CRM раскладывается на отправки Telegram."""

from app.gateway.send_plan import (
    ALBUM_LIMIT,
    CAPTION_LIMIT,
    MediaStep,
    OutgoingFile,
    TextStep,
    plan,
    split_text,
    utf16_len,
)


def _file(name: str, kind: str) -> OutgoingFile:
    return OutgoingFile(path=f"/tmp/{name}", file_name=name, mime_type=None, kind=kind)


def test_text_only_is_one_step():
    result = plan("Здравствуйте!", [])
    assert [type(step) for step in result.steps] == [TextStep]
    assert result.message_count == 1


def test_empty_message_has_no_steps():
    assert plan("   ", []).steps == []


def test_caption_goes_to_first_file():
    result = plan("Ваш разбор", [_file("a.pdf", "document")])
    assert len(result.steps) == 1
    step = result.steps[0]
    assert isinstance(step, MediaStep)
    assert step.caption == "Ваш разбор"


def test_long_text_is_sent_before_files_without_caption():
    text = "а" * (CAPTION_LIMIT + 1)
    result = plan(text, [_file("a.jpg", "photo")])
    assert isinstance(result.steps[0], TextStep)
    media = result.steps[1]
    assert isinstance(media, MediaStep) and media.caption is None


def test_voice_is_always_alone_and_marked_voice():
    result = plan(None, [_file("a.jpg", "photo"), _file("v.ogg", "voice"), _file("b.jpg", "photo")])
    kinds = [(len(s.files), s.voice, s.group) for s in result.steps if isinstance(s, MediaStep)]
    # фото — одним альбомом, голосовое — отдельно, порядок — по первому появлению
    assert kinds == [(2, False, "visual"), (1, True, None)]


def test_photos_and_videos_share_album_documents_do_not():
    files = [
        _file("a.jpg", "photo"),
        _file("doc.pdf", "document"),
        _file("clip.mp4", "video"),
        _file("song.mp3", "audio"),
    ]
    result = plan("подпись", files)
    groups = [s.group for s in result.steps if isinstance(s, MediaStep)]
    assert groups == ["visual", "document", "audio"]
    assert result.steps[0].caption == "подпись"
    assert [f.file_name for f in result.steps[0].files] == ["a.jpg", "clip.mp4"]


def test_heic_and_webp_go_as_documents():
    result = plan(
        None, [_file("a.heic", "photo"), _file("b.webp", "photo"), _file("c.png", "photo")]
    )
    assert [(s.group, [f.file_name for f in s.files]) for s in result.steps] == [
        ("document", ["a.heic", "b.webp"]),
        ("visual", ["c.png"]),
    ]


def test_albums_are_split_by_ten():
    files = [_file(f"{i}.jpg", "photo") for i in range(23)]
    result = plan(None, files)
    sizes = [len(s.files) for s in result.steps]
    assert sizes == [ALBUM_LIMIT, ALBUM_LIMIT, 3]
    assert result.message_count == 23


def test_video_note_gif_and_sticker_are_single():
    files = [_file("r.mp4", "video_note"), _file("g.gif", "animation"), _file("s.webp", "sticker")]
    result = plan(None, files)
    assert [len(s.files) for s in result.steps] == [1, 1, 1]


def test_split_text_respects_utf16_limit_and_words():
    emoji_text = ("😀 слово " * 1200).strip()
    parts = split_text(emoji_text, limit=4096)
    assert len(parts) > 1
    assert all(utf16_len(part) <= 4096 for part in parts)
    # слова не режутся пополам
    assert all(not part.endswith("сло") for part in parts)
    assert "".join(parts).replace(" ", "") == emoji_text.replace(" ", "")


def test_short_text_not_split():
    assert split_text("коротко") == ["коротко"]
