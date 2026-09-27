"""Перечисления домена.

Значения совпадают с тем, что уходит в API и приходит во фронтенд — без переводов
в промежуточных слоях. Русские подписи живут во фронтенде, в одном месте.
"""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"  # Руководитель
    MANAGER = "manager"  # Менеджер


class FunnelStage(StrEnum):
    """Этап воронки задаётся аккаунту, а не клиенту: клиент на том этапе,
    в чат которого он пишет."""

    WARMUP = "warmup"  # Прогрев
    DIAGNOSTIC = "diagnostic"  # Диагностика
    SALES = "sales"  # Продажи


class BirthTimeApprox(StrEnum):
    """Приблизительное время рождения, когда точное неизвестно.

    Точную карту по нему не построить — дома зависят от минут, — но для
    астролога это всё же больше, чем пустое поле, поэтому «неполными данными»
    карточка остаётся до тех пор, пока не появится точное время.
    """

    MORNING = "morning"
    DAY = "day"
    EVENING = "evening"
    NIGHT = "night"


class AccountStatus(StrEnum):
    PENDING = "pending"  # заведён, вход не завершён
    CONNECTED = "connected"
    ERROR = "error"  # сессия отозвана или связь потеряна
    DISCONNECTED = "disconnected"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class AuthorKind(StrEnum):
    CLIENT = "client"
    MANAGER = "manager"
    USERBOT = "userbot"  # рассылка воронки, ушедшая мимо CRM
    SYSTEM = "system"


class MessageKind(StrEnum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    VOICE = "voice"
    # Зарезервированы: база и этот код их понимают, но пока не записывают.
    # Предыдущая версия этих значений не знает, и откат на неё (D-28) уронил бы
    # чаты с такими сообщениями. Точный вид медиа хранится у вложения
    # (`attachments.kind`) — по нему и подписываются «Аудио» и «Стикер».
    # Записывать их можно не раньше следующего релиза: его откат придёт на этот
    # код, который их уже читает.
    AUDIO = "audio"
    STICKER = "sticker"
    SERVICE = "service"  # служебное: клиент не видит


class AttachmentKind(StrEnum):
    """Вид вложения — как его показать и как отправить в Telegram.

    Хранится строкой, а не типом Postgres: новые виды Telegram (а они
    появляются) не должны требовать миграции с ALTER TYPE.
    """

    PHOTO = "photo"
    VIDEO = "video"
    VIDEO_NOTE = "video_note"  # «кружочек»
    ANIMATION = "animation"  # GIF: Telegram присылает его как беззвучное mp4
    VOICE = "voice"
    AUDIO = "audio"
    STICKER = "sticker"
    DOCUMENT = "document"


# Вид сообщения по виду его первого вложения. Вид сообщения — грубый: только
# значения, которые читает и предыдущая версия (см. MessageKind).
_MESSAGE_KIND_BY_ATTACHMENT: dict[str, MessageKind] = {
    AttachmentKind.PHOTO: MessageKind.PHOTO,
    AttachmentKind.VIDEO: MessageKind.VIDEO,
    AttachmentKind.VIDEO_NOTE: MessageKind.VIDEO,
    AttachmentKind.ANIMATION: MessageKind.VIDEO,
    AttachmentKind.VOICE: MessageKind.VOICE,
    AttachmentKind.AUDIO: MessageKind.DOCUMENT,
    AttachmentKind.STICKER: MessageKind.DOCUMENT,
    AttachmentKind.DOCUMENT: MessageKind.DOCUMENT,
}


def message_kind_for(attachment_kind: str | None) -> MessageKind:
    return _MESSAGE_KIND_BY_ATTACHMENT.get(attachment_kind or "", MessageKind.DOCUMENT)


class AttachmentStatus(StrEnum):
    """Есть ли файл в нашем хранилище.

    Большой файл клиента не держит сообщение: оно появляется сразу, а файл
    докачивается в фоне. Слишком большой не качается вовсе — но и не
    пропадает молча: менеджер видит, что файл есть и где его открыть.
    """

    READY = "ready"
    PENDING = "pending"  # докачивается шлюзом
    FAILED = "failed"  # попытки исчерпаны, причина в meta.error
    TOO_LARGE = "too_large"  # больше лимита CRM — только в Telegram


class MessageStatus(StrEnum):
    """Статуса «доставлено» нет: MTProto его не отдаёт.

    Часы — в очереди, одна галочка — отправлено, две — прочитано, крестик — ошибка.
    """

    QUEUED = "queued"
    SENT = "sent"
    READ = "read"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    DONE = "done"
    FAILED = "failed"


class PaymentMethod(StrEnum):
    LINK = "link"  # ссылка провайдера, включается вместе с Робокассой
    REQUISITES = "requisites"  # реквизиты, подтверждение вручную по приложенному чеку


class DealStatus(StrEnum):
    DRAFT = "draft"  # создана, ещё не отправлена в чат
    AWAITING = "awaiting"  # ждёт оплаты
    PAID = "paid"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ALLOWED_DEAL_TRANSITIONS: dict[DealStatus, set[DealStatus]] = {
    DealStatus.DRAFT: {DealStatus.AWAITING, DealStatus.CANCELLED},
    DealStatus.AWAITING: {DealStatus.PAID, DealStatus.CANCELLED, DealStatus.EXPIRED},
    DealStatus.EXPIRED: {DealStatus.CANCELLED},
    DealStatus.PAID: set(),
    DealStatus.CANCELLED: set(),
}


class DealEventKind(StrEnum):
    CREATED = "created"
    SENT = "sent"
    EDITED = "edited"  # событие, а не статус: из «изменена» некуда идти дальше
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PAID = "paid"
    REMINDED = "reminded"


class PaidSource(StrEnum):
    MANUAL = "manual"
    PROVIDER = "provider"


class NotificationKind(StrEnum):
    DEAL_PAID = "deal_paid"
    ACCOUNT_ASSIGNED = "account_assigned"
    ACCOUNT_UNASSIGNED = "account_unassigned"
    ACCOUNT_ERROR = "account_error"
    # Провайдер прислал оплату, но сумма не сошлась со сделкой — например,
    # клиент заплатил по ссылке, устаревшей после редактирования сделки.
    # Деньги у провайдера, в CRM сделка не закрыта — без этого уведомления
    # об этом узнали бы только по логам сервера.
    PAYMENT_MISMATCH = "payment_mismatch"
    # Провайдер прислал оплату по сделке, которая уже не в статусе, допускающем
    # оплату (отменена/черновик) — гонка "отменили в момент оплаты" или сделку
    # отменили уже после того, как клиент начал платить. Деньги у провайдера,
    # сверка — вручную по данным личного кабинета Робокассы.
    PAYMENT_ORPHANED = "payment_orphaned"


class ActorKind(StrEnum):
    USER = "user"
    SYSTEM = "system"
    GATEWAY = "gateway"


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    """Postgres-тип должен хранить значения ('pending'), а не имена ('PENDING').

    По умолчанию SQLAlchemy берёт имена — и server_default перестаёт совпадать
    с типом. Передаётся во все Enum-колонки через values_callable.
    """
    return [member.value for member in enum_cls]
