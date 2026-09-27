"""Вид сообщения и подпись в списке чатов.

Вид сообщения записывается только из набора, который читает предыдущая версия:
автооткат выкатки (deploy/deploy.sh) возвращает старый код на ту же базу, и
незнакомое ему значение уронило бы чаты с таким сообщением. Точный вид медиа
хранится у вложения — по нему и подписываются «Аудио», «Стикер» и прочее.
"""

from app.models import AttachmentKind, Direction, MessageKind
from app.models.enums import message_kind_for
from app.services.conversation_service import _preview

# Виды сообщений, которые понимает версия до релиза 27.09.2026. Расширять —
# только когда на проде стоит версия, которая новые значения уже читает.
PREVIOUS_RELEASE_KINDS = {"text", "photo", "video", "document", "voice", "service"}


def test_every_attachment_kind_maps_to_a_kind_the_previous_release_reads() -> None:
    for kind in AttachmentKind:
        assert message_kind_for(kind.value).value in PREVIOUS_RELEASE_KINDS, kind


def test_unknown_or_missing_attachment_kind_is_a_document() -> None:
    assert message_kind_for(None) is MessageKind.DOCUMENT
    assert message_kind_for("hologram") is MessageKind.DOCUMENT


def test_voice_stays_voice_and_music_is_not_voice() -> None:
    assert message_kind_for("voice") is MessageKind.VOICE
    assert message_kind_for("audio") is MessageKind.DOCUMENT


def test_preview_names_media_by_attachment() -> None:
    assert _preview(None, Direction.IN, MessageKind.DOCUMENT, "audio") == "Аудио"
    assert _preview(None, Direction.OUT, MessageKind.DOCUMENT, "sticker") == "Вы: Стикер"
    assert _preview(None, Direction.IN, MessageKind.VIDEO, "video_note") == "Видеосообщение"
    assert _preview(None, Direction.IN, MessageKind.VIDEO, "animation") == "GIF"
    assert _preview(None, Direction.IN, MessageKind.DOCUMENT, "document") == "Файл"
    assert _preview(None, Direction.IN, MessageKind.VOICE, "voice") == "Голосовое сообщение"
    # Вложения без вида (заведены до него) — подпись по виду сообщения.
    assert _preview(None, Direction.IN, MessageKind.PHOTO, None) == "Фото"


def test_preview_prefers_text() -> None:
    assert _preview("Спасибо!", Direction.IN, MessageKind.DOCUMENT, "audio") == "Спасибо!"
    assert _preview(None, None, MessageKind.DOCUMENT, "audio") is None
