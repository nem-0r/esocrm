from datetime import datetime, time

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, PKMixin, TimestampMixin
from app.models.enums import UserRole, enum_values


class User(Base, PKMixin, TimestampMixin):
    __tablename__ = "users"

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # citext создаётся в миграции: логин регистронезависим, иначе блокировку
    # обходят сменой регистра.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True, values_callable=enum_values),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    accepting_leads: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    avatar_color: Mapped[str] = mapped_column(String(7), nullable=False, server_default="#7A6BE0")

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # График работы сотрудника (ТЗ Б.3). Недельный повтор, а не календарь смен:
    # смены с заменами, отпусками и обменами — это система учёта рабочего
    # времени, а её в задаче нет. Порог перехода: когда понадобятся исключения
    # на конкретные даты, здесь появится отдельная таблица.
    schedule_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Дни недели по ISO: 1 — понедельник, 7 — воскресенье.
    work_days: Mapped[list[int] | None] = mapped_column(JSONB)
    work_start: Mapped[time | None] = mapped_column(Time)
    work_end: Mapped[time | None] = mapped_column(Time)

    invite_token_hash: Mapped[str | None] = mapped_column(String(255))
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invite_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # foreign_keys обязателен: в account_managers два внешних ключа на users
    # (user_id и assigned_by_id). Без явного указания SQLAlchemy не может выбрать,
    # по какому строить связь, и падает на первом же запросе.
    account_links: Mapped[list["AccountManager"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="AccountManager.user_id",
    )

    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
        Index("ix_users_last_seen_at", "last_seen_at"),
        Index("ix_users_invite_token_hash", "invite_token_hash"),
    )

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def invite_pending(self) -> bool:
        return self.accepted_at is None and self.invite_token_hash is not None


class AuthSession(Base, PKMixin, TimestampMixin):
    """Сессия входа сотрудника. Отзывается при смене пароля и деактивации."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_auth_sessions_user_id_revoked_at", "user_id", "revoked_at"),)


class LoginAttempt(Base, PKMixin, TimestampMixin):
    """Блокировка после 5 неудач за 30 минут считается по этой таблице."""

    __tablename__ = "login_attempts"

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(INET)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_login_attempts_email_created_at", "email", "created_at"),)
