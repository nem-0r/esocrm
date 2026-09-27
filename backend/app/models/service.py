from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, SmallInteger, String, Text, func
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PKMixin, TimestampMixin


class Service(Base, PKMixin, TimestampMixin):
    """Справочник услуг: то, что менеджер выбирает в окне оплаты.

    Цена — подсказка, а не закон: в сделке её можно поправить, а в позиции
    сделки остаётся снимок цены на момент продажи (`deal_items.list_price`).
    Поэтому переименование или новая цена здесь не переписывают историю.
    """

    __tablename__ = "services"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Копейки. Пусто — «цена по договорённости»: название подставится, сумму
    # менеджер впишет сам.
    price: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # Две «Натальные карты» в списке — это уже не справочник, а путаница:
        # какую выбрать и куда потом считать продажи. Регистр не различаем,
        # удалённые не мешают завести услугу с тем же именем заново.
        Index(
            "uq_services_name_alive",
            sa_text("lower(name)"),
            unique=True,
            postgresql_where=sa_text("deleted_at IS NULL"),
        ),
    )
