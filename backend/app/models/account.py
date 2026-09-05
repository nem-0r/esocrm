from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, PKMixin, TimestampMixin
from app.models.enums import AccountStatus, FunnelStage, enum_values


class TelegramAccount(Base, PKMixin, TimestampMixin):
    """Личный Telegram-аккаунт, подключённый второй сессией.

    Воронки продолжают работать со своей стороны — их сессию мы не трогаем.
    Сессия CRM хранится здесь зашифрованной: файл сессии равен полному доступу
    к аккаунту, это главный секрет системы.
    """

    __tablename__ = "telegram_accounts"

    title: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    funnel_stage: Mapped[FunnelStage] = mapped_column(
        Enum(FunnelStage, name="funnel_stage", native_enum=True, values_callable=enum_values),
        nullable=False,
    )

    api_id: Mapped[int] = mapped_column(Integer, nullable=False)
    api_hash_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    session_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    tg_user_id: Mapped[int | None] = mapped_column(BigInteger)
    tg_username: Mapped[str | None] = mapped_column(String(120))

    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status", native_enum=True, values_callable=enum_values),
        nullable=False,
        server_default=AccountStatus.PENDING.value,
    )
    status_reason: Mapped[str | None] = mapped_column(String(500))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_synced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Аренда аккаунта процессом шлюза. Процесс пропал — аренда истекла —
    # аккаунт подхватывает соседний. Так шлюз масштабируется контейнером.
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    manager_links: Mapped[list["AccountManager"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Номер занят только живым аккаунтом. Отключённый остаётся в базе ради
        # переписки и сделок, но не должен мешать подключить тот же номер заново.
        Index(
            "uq_telegram_accounts_phone_alive",
            "phone",
            unique=True,
            postgresql_where=sa_text("deleted_at IS NULL"),
        ),
        Index("ix_telegram_accounts_active_lease", "is_active", "lease_until"),
        Index("ix_telegram_accounts_status", "status"),
    )

    @property
    def needs_attention(self) -> bool:
        """«Требуют внимания»: сессия прервана либо на аккаунт не назначен менеджер."""
        return self.status in (AccountStatus.ERROR, AccountStatus.PENDING) or not self.manager_links


class AccountManager(Base):
    """Многие-ко-многим: все назначенные менеджеры видят и ведут все диалоги аккаунта."""

    __tablename__ = "account_managers"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    assigned_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    account: Mapped[TelegramAccount] = relationship(back_populates="manager_links")
    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="account_links", foreign_keys=[user_id]
    )

    __table_args__ = (
        PrimaryKeyConstraint("account_id", "user_id", name="pk_account_managers"),
        Index("ix_account_managers_user_id", "user_id"),
    )


class GatewayWorker(Base):
    """Процесс шлюза. Отмечается каждые 10 секунд, продлевая аренду своих аккаунтов."""

    __tablename__ = "gateway_workers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    capacity: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="25")
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TelegramPeer(Base, TimestampMixin):
    """Собеседник, как его видит конкретный аккаунт.

    `access_hash` — не свойство человека, а пропуск, выданный **этому** аккаунту.
    Хэш, полученный аккаунтом А, для аккаунта Б недействителен, поэтому ключ
    составной: (аккаунт, пользователь Telegram). Без сохранённого хэша после
    перезапуска нельзя ответить тому, кто не всплыл в свежем списке диалогов —
    именно на этом обычно ломаются самодельные интеграции.
    """

    __tablename__ = "telegram_peers"

    account_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False
    )
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    access_hash: Mapped[int | None] = mapped_column(BigInteger)

    username: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    is_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    # Карточка клиента, если она уже заведена. Держим здесь, чтобы входящее
    # сообщение находило клиента одним запросом, без поиска по telegram_id.
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        PrimaryKeyConstraint("account_id", "tg_user_id", name="pk_telegram_peers"),
        Index("ix_telegram_peers_client", "client_id"),
    )
