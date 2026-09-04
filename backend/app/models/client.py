from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PKMixin, TimestampMixin
from app.models.enums import BirthTimeApprox, enum_values

# (месяц, день начала знака, знак). Знак, начинающийся в этом месяце;
# если число меньше дня начала — знак предыдущего месяца.
ZODIAC_BOUNDS: list[tuple[int, int, str]] = [
    (1, 20, "Водолей"),
    (2, 19, "Рыбы"),
    (3, 21, "Овен"),
    (4, 20, "Телец"),
    (5, 21, "Близнецы"),
    (6, 21, "Рак"),
    (7, 23, "Лев"),
    (8, 23, "Дева"),
    (9, 23, "Весы"),
    (10, 23, "Скорпион"),
    (11, 22, "Стрелец"),
    (12, 22, "Козерог"),
]


def zodiac_for(birth: date | None) -> str | None:
    """Знак зодиака вычисляется из даты рождения, а не вводится руками."""
    if birth is None:
        return None
    start_day, sign = ZODIAC_BOUNDS[birth.month - 1][1:]
    if birth.day >= start_day:
        return sign
    # Для января индекс -1 даёт декабрь, то есть Козерога — это верно.
    return ZODIAC_BOUNDS[birth.month - 2][2]


class Client(Base, PKMixin, TimestampMixin):
    """Клиент существует в одном экземпляре и опознаётся по telegram_id.

    Переход в другой чат воронки не создаёт новую карточку — добавляется диалог.
    Идентификатор `id` показывается в интерфейсе как uid.
    """

    __tablename__ = "clients"

    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    tg_username: Mapped[str | None] = mapped_column(String(120))
    tg_first_name: Mapped[str | None] = mapped_column(String(255))
    tg_last_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))

    # Бот-маршрутизатор передаёт клиента с кодом в первом сообщении.
    # Данные, собранные ботом, лежат в его базе — интеграция после MVP.
    source_code: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(120))

    birth_date: Mapped[date | None] = mapped_column(Date)
    birth_time: Mapped[time | None] = mapped_column(Time)
    birth_time_approx: Mapped[BirthTimeApprox | None] = mapped_column(
        Enum(
            BirthTimeApprox,
            name="birth_time_approx",
            values_callable=enum_values,
        )
    )
    birth_city: Mapped[str | None] = mapped_column(String(255))
    zodiac_sign: Mapped[str | None] = mapped_column(String(20))

    # Согласия по 152-ФЗ. Собирает бот воронки при первом обращении, CRM их
    # хранит и показывает: рассылку нельзя слать тому, кто от неё отказался.
    pdn_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pdn_consent_version: Mapped[str | None] = mapped_column(String(20))
    marketing_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    marketing_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Через какой аккаунт клиент пришёл впервые — нужно в списке клиентов (ТЗ п. 5.2).
    created_via_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_accounts.id", ondelete="SET NULL")
    )

    first_contact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_clients_first_contact_at", "first_contact_at"),
        Index("ix_clients_phone", "phone"),
        Index("ix_clients_source_code", "source_code"),
    )

    @property
    def name(self) -> str:
        """Как звать клиента. Имя, введённое менеджером, вытесняет всё остальное.

        Клиент может не указать в Telegram ни имени, ни @username — тогда
        остаётся публичный id. Менеджер вписывает имя руками, и с этого момента
        его видят все (ТЗ п. 5.3).
        """
        if self.display_name:
            return self.display_name
        parts = [p for p in (self.tg_first_name, self.tg_last_name) if p]
        if parts:
            return " ".join(parts)
        return f"@{self.tg_username}" if self.tg_username else str(self.telegram_id)

    @property
    def data_complete(self) -> bool:
        """«Неполные данные» на доске: нет времени или города рождения."""
        return bool(self.birth_date and self.birth_time and self.birth_city)


class Note(Base, PKMixin, TimestampMixin):
    __tablename__ = "notes"

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_notes_client_id_created_at", "client_id", "created_at"),)
