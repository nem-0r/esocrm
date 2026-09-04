from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, PKMixin, TimestampMixin
from app.models.enums import (
    AuthorKind,
    Direction,
    MessageKind,
    MessageStatus,
    OutboxStatus,
    enum_values,
)


class Conversation(Base, PKMixin, TimestampMixin):
    """Диалог — это пара «клиент + аккаунт». Один клиент может вести несколько диалогов.

    Счётчики денормализованы намеренно: список чатов — самый горячий экран,
    пересчитывать его на каждый запрос нельзя.
    """

    __tablename__ = "conversations"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Ответственный — кто первым ответил. Кнопка «Передать» меняет его вручную.
    responsible_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    responsible_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_client_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_manager_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Пусто = ответ дан. Время ожидания не хранится, считается как now() - это поле.
    awaiting_reply_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Когда менеджер открыл чат и увидел ожидание. Пока это время новее начала
    # ожидания — плашку не показываем: она своё отработала (ТЗ п. 2.1). Новое
    # обращение клиента после ответа начнёт новое ожидание, и плашка вернётся.
    awaiting_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_blocked_by_client: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Диалог в этом боте закончился — клиент ушёл дальше по воронке. Закрытый
    # диалог не ждёт ответа и не попадает в счётчики, но остаётся в карточке.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    client: Mapped["Client"] = relationship(lazy="joined")  # noqa: F821
    account: Mapped["TelegramAccount"] = relationship(lazy="joined")  # noqa: F821

    __table_args__ = (
        UniqueConstraint("client_id", "account_id", name="uq_conversations_client_account"),
        Index(
            "ix_conversations_account_last_message", "account_id", sa_text("last_message_at DESC")
        ),
        Index(
            "ix_conversations_responsible_last_message",
            "responsible_id",
            sa_text("last_message_at DESC"),
        ),
        Index(
            "ix_conversations_awaiting",
            "awaiting_reply_since",
            postgresql_where=sa_text("awaiting_reply_since IS NOT NULL"),
        ),
        Index("ix_conversations_client_id", "client_id"),
    )


class Message(Base, PKMixin, TimestampMixin):
    """Сообщение в диалоге.

    Отличие менеджера от юзербота: перед отправкой CRM генерирует random_id и шлёт
    с ним. Исходящее событие с неизвестным нам random_id — это отправила воронка.
    Заложено с первого дня: задним числом не восстановить.
    """

    __tablename__ = "messages"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger)

    direction: Mapped[Direction] = mapped_column(
        Enum(Direction, name="message_direction", native_enum=True, values_callable=enum_values),
        nullable=False,
    )
    author_kind: Mapped[AuthorKind] = mapped_column(
        Enum(AuthorKind, name="author_kind", native_enum=True, values_callable=enum_values),
        nullable=False,
    )
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    kind: Mapped[MessageKind] = mapped_column(
        Enum(MessageKind, name="message_kind", native_enum=True, values_callable=enum_values),
        nullable=False,
        server_default=MessageKind.TEXT.value,
    )
    text: Mapped[str | None] = mapped_column(Text)

    # Служебное сообщение: видно только внутри CRM, клиенту не уходит.
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, name="message_status", native_enum=True, values_callable=enum_values),
        nullable=False,
        server_default=MessageStatus.SENT.value,
    )
    error_text: Mapped[str | None] = mapped_column(Text)
    random_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    reply_to_tg_id: Mapped[int | None] = mapped_column(BigInteger)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan", lazy="selectin"
    )
    author: Mapped["User | None"] = relationship(lazy="joined")  # noqa: F821

    __table_args__ = (
        # Частичный уникальный индекс: повторная доставка события Telegram
        # не создаёт дубль. Служебные сообщения без tg_message_id не мешают.
        Index(
            "uq_messages_conversation_tg_id",
            "conversation_id",
            "tg_message_id",
            unique=True,
            postgresql_where=sa_text("tg_message_id IS NOT NULL"),
        ),
        Index("ix_messages_conversation_created", "conversation_id", sa_text("created_at DESC")),
        Index("ix_messages_author_created", "author_id", "created_at"),
        Index(
            "ix_messages_text_fts",
            sa_text("to_tsvector('russian', coalesce(text, ''))"),
            postgresql_using="gin",
        ),
    )


class Attachment(Base, PKMixin, TimestampMixin):
    """Файл из чата. Скачивается к себе: ссылка Telegram не вечная."""

    __tablename__ = "attachments"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # Дублируется намеренно: вкладка «Материалы» в карточке клиента
    # выбирается без объединения трёх таблиц.
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    # Позволяет переслать файл повторно, не выгружая его заново. Бессрочного
    # доступа не даёт, поэтому сам файл всё равно лежит в нашем хранилище.
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    message: Mapped[Message] = relationship(back_populates="attachments")

    __table_args__ = (
        Index("ix_attachments_client_created", "client_id", sa_text("created_at DESC")),
        Index("ix_attachments_message_id", "message_id"),
    )


class Outbox(Base, PKMixin, TimestampMixin):
    """Очередь исходящих.

    Забирается с блокировкой строки — два процесса не отправят одно дважды.
    Внутри диалога отправка строго последовательная, иначе сообщения придут вперемешку.
    При FloodWait срок сдвигается на время, которое вернул сам Telegram.
    """

    __tablename__ = "outbox"

    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[OutboxStatus] = mapped_column(
        Enum(OutboxStatus, name="outbox_status", native_enum=True, values_callable=enum_values),
        nullable=False,
        server_default=OutboxStatus.PENDING.value,
    )
    error_text: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_outbox_account_status_next", "account_id", "status", "next_attempt_at"),
        Index("ix_outbox_conversation_id", "conversation_id"),
    )
