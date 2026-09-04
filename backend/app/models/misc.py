from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PKMixin, TimestampMixin
from app.models.enums import ActorKind, NotificationKind, enum_values


class Template(Base, PKMixin, TimestampMixin):
    """Шаблон сообщения. Вставляется в поле ввода дописыванием, а не заменой текста."""

    __tablename__ = "templates"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Пусто = общий шаблон, виден всем.
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_templates_owner_sort", "owner_id", "sort_order"),)


class Notification(Base, PKMixin, TimestampMixin):
    """В MVP — только об оплатах и о назначении-снятии менеджеров.

    Уведомления о назначении в настройках профиля не отключаются: так на доске.
    """

    __tablename__ = "notifications"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(
            NotificationKind,
            name="notification_kind",
            native_enum=True,
            values_callable=enum_values,
        ),
        nullable=False,
    )
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_notifications_user_read_created", "user_id", "read_at", sa_text("created_at DESC")
        ),
    )


class SearchHistory(Base, PKMixin, TimestampMixin):
    """История поиска: последние 10 записей на пользователя, старые удаляются."""

    __tablename__ = "search_history"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        Index("ix_search_history_user_created", "user_id", sa_text("created_at DESC")),
    )


class EventLog(Base, PKMixin, TimestampMixin):
    """Аудит. Только добавление: изменений и удалений нет."""

    __tablename__ = "event_log"

    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_kind: Mapped[ActorKind] = mapped_column(
        Enum(ActorKind, name="actor_kind", native_enum=True, values_callable=enum_values),
        nullable=False,
        server_default=ActorKind.USER.value,
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        Index("ix_event_log_entity", "entity_type", "entity_id", sa_text("created_at DESC")),
        Index("ix_event_log_actor_created", "actor_id", sa_text("created_at DESC")),
    )


class Setting(Base):
    """Настройки системы. Меняются руководителем без релиза."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


DEFAULT_SETTINGS: dict[str, Any] = {
    "awaiting_banner_minutes": 30,
    "response_time_goal_minutes": 15,
    "deal_link_ttl_days": 7,
    "working_hours_enabled": False,
    "working_hours_start": "09:00",
    "working_hours_end": "21:00",
    "working_days": [1, 2, 3, 4, 5, 6],
    "timezone": "Europe/Moscow",
    # Глубина, на которую подтягивается переписка при подключении аккаунта.
    # ТЗ п. 4.2 задаёт не срок, а дату: «с 1 января 2026». Держим именно дату —
    # срок в днях уезжал бы каждый день и через год начал бы терять историю.
    # Поле в днях осталось запасным: если дата пустая, берётся оно.
    "history_sync_from": "2026-01-01",
    "history_sync_days": 365,
    # Чек по 54-ФЗ для оплаты по ссылке (docs/11-payments-architecture.md, разд. 3).
    # Значения зависят от системы налогообложения компании — вопрос к бухгалтеру,
    # не константа в коде. Пока ответа нет, стоит нейтральная заглушка «без НДС».
    "robokassa_sno": "usn_income",
    "robokassa_tax": "none",
}


class DailyStat(Base):
    """Предагрегат статистики. Отчёт за период складывается из готовых строк —
    так требование «аналитика меньше двух секунд» держится на любом диапазоне.

    user_id = 0 — строка «по всей компании».
    """

    __tablename__ = "daily_stats"

    day: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    deals_paid_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    deals_paid_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    new_clients: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active_conversations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    response_time_sum_sec: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    response_time_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (PrimaryKeyConstraint("day", "user_id", name="pk_daily_stats"),)
