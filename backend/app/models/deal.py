from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, PKMixin, TimestampMixin
from app.models.enums import (
    DealEventKind,
    DealStatus,
    PaidSource,
    PaymentMethod,
    enum_values,
)


class PaymentRequisite(Base, PKMixin, TimestampMixin):
    """Справочник счетов получателя. Ведёт руководитель в настройках."""

    __tablename__ = "payment_requisites"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(255))
    account_masked: Mapped[str | None] = mapped_column(String(64))

    # Структура появилась, когда реквизитов стало одиннадцать по шести
    # направлениям. Менеджер обязан выбрать тот счёт, по которому клиент
    # реально сможет заплатить: казахстанская карта клиенту из России —
    # это несостоявшаяся продажа. Поэтому страна и способ — поля, а не
    # строчка внутри текста.
    country: Mapped[str | None] = mapped_column(String(40))
    method: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str | None] = mapped_column(String(40))
    number: Mapped[str | None] = mapped_column(String(120))
    holder: Mapped[str | None] = mapped_column(String(255))
    iban: Mapped[str | None] = mapped_column(String(64))
    bic: Mapped[str | None] = mapped_column(String(32))

    # Полный текст реквизитов — именно он уходит клиенту в чат. Собирается из
    # полей выше, но остаётся редактируемым: банки требуют своих формулировок.
    details_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Deal(Base, PKMixin, TimestampMixin):
    """Сделка (оплата). `id` — это номер DEAL-1042.

    Оплата по реквизитам сверяется по чеку, который менеджер прикладывает файлом
    при подтверждении (`receipt_storage_key` и связанные поля ниже).
    """

    __tablename__ = "deals"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    # «Продажа засчитывается тому, кто создал оплату. Снятие менеджера
    # не меняет прошлую статистику» — поэтому храним снимком, а не выводим из связей.
    sold_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, name="payment_method", native_enum=True, values_callable=enum_values),
        nullable=False,
    )
    requisite_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_requisites.id", ondelete="RESTRICT")
    )
    requisites_snapshot: Mapped[str | None] = mapped_column(Text)
    # Текст, которым менеджер сопровождает счёт. Уходит клиенту перед реквизитами
    # или ссылкой — одинаково для обоих способов оплаты (ТЗ п. 4.4 и 4.5).
    intro_text: Mapped[str | None] = mapped_column(Text)
    # На какой реквизит деньги пришли фактически. Может отличаться от того, что
    # был в счёте: платят и с чужой карты, и другим способом, а сверять выписку
    # надо с тем счётом, куда деньги дошли.
    paid_to_requisite_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_requisites.id", ondelete="SET NULL")
    )
    paid_to_requisite: Mapped["PaymentRequisite | None"] = relationship(
        foreign_keys=[paid_to_requisite_id], lazy="joined"
    )

    status: Mapped[DealStatus] = mapped_column(
        Enum(DealStatus, name="deal_status", native_enum=True, values_callable=enum_values),
        nullable=False,
        server_default=DealStatus.DRAFT.value,
    )
    total_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    # Text, не String: ссылка несёт URL-кодированный чек по 54-ФЗ и легко
    # превышает 500 символов на кириллических названиях услуг (было найдено
    # живым запросом при первом реальном создании ссылки — падало на границе).
    payment_url: Mapped[str | None] = mapped_column(Text)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255))

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    paid_source: Mapped[PaidSource | None] = mapped_column(
        Enum(PaidSource, name="paid_source", native_enum=True, values_callable=enum_values)
    )

    # Файл чека, приложенный менеджером при подтверждении. Без него оплату
    # подтвердить нельзя (заменил номер чека вручную — так проще проверить
    # реальный документ, а не поверить сотруднику на слово). Своей таблицы
    # вложений не заводим: у сделки ровно один чек, а не произвольный список,
    # так что четыре колонки здесь дешевле лишней связи (см. docs/11, разд. 6.1
    # про то же рассуждение для будущей payment_receipts).
    receipt_storage_key: Mapped[str | None] = mapped_column(String(500))
    receipt_file_name: Mapped[str | None] = mapped_column(String(255))
    receipt_mime_type: Mapped[str | None] = mapped_column(String(120))
    receipt_size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    edit_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    items: Mapped[list["DealItem"]] = relationship(
        back_populates="deal",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DealItem.position",
    )
    client: Mapped["Client"] = relationship(lazy="joined")  # noqa: F821
    # Канал сделки нужен в каждой строке списка (ТЗ п. 4.6), а у диалога аккаунт
    # тоже грузится сразу — так что вложенный account приходит тем же запросом.
    conversation: Mapped["Conversation"] = relationship(lazy="joined")  # noqa: F821

    __table_args__ = (
        Index("ix_deals_client_created", "client_id", text("created_at DESC")),
        Index("ix_deals_sold_by_paid", "sold_by_id", "paid_at"),
        Index("ix_deals_status_expires", "status", "expires_at"),
        Index("ix_deals_conversation_status", "conversation_id", "status"),
    )

    @property
    def number(self) -> str:
        return f"DEAL-{self.id}"

    @property
    def title(self) -> str:
        """В строке списка: название услуги; если их несколько — самая дорогая и «и др.»."""
        if not self.items:
            return "Без услуг"
        top = max(self.items, key=lambda i: i.amount)
        return f"{top.name} и др." if len(self.items) > 1 else top.name


class DealItem(Base, PKMixin, TimestampMixin):
    """Позиция сделки. В MVP название и сумму менеджер вводит вручную —
    справочника услуг нет."""

    __tablename__ = "deal_items"

    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    deal: Mapped[Deal] = relationship(back_populates="items")

    __table_args__ = (Index("ix_deal_items_deal_id", "deal_id"),)


class DealEvent(Base, PKMixin, TimestampMixin):
    """Журнал сделки — то, что видно в карточке как «Журнал».

    Для изменения и отмены комментарий обязателен: это требование доски.
    """

    __tablename__ = "deal_events"

    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[DealEventKind] = mapped_column(
        Enum(DealEventKind, name="deal_event_kind", native_enum=True, values_callable=enum_values),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (Index("ix_deal_events_deal_created", "deal_id", "created_at"),)


class PaymentEvent(Base, PKMixin, TimestampMixin):
    """События платёжного провайдера.

    Таблица заводится сразу, хотя Робокассы ещё нет: уникальность provider_event_id —
    единственное, что не даст засчитать оплату дважды при повторной доставке вебхука.
    """

    __tablename__ = "payment_events"

    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_payment_events_deal_id", "deal_id"),)
