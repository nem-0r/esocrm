"""Структура реквизитов: страна, способ, тип, номер, держатель, IBAN, BIC

Реквизитов стало одиннадцать по шести направлениям, и выбирать из плоского
списка стало нельзя: менеджеру нужен тот счёт, по которому клиент сможет
заплатить. Все колонки nullable — старые записи остаются валидными.

Revision ID: e58c93df1a44
Revises: d47b21c8fa30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e58c93df1a44"
down_revision: str | None = "d47b21c8fa30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    ("country", sa.String(40)),
    ("method", sa.String(120)),
    ("kind", sa.String(40)),
    ("number", sa.String(120)),
    ("holder", sa.String(255)),
    ("iban", sa.String(64)),
    ("bic", sa.String(32)),
]


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("payment_requisites", sa.Column(name, type_, nullable=True))
    # Список группируется по стране — индекс под сортировку выдачи.
    op.create_index(
        "ix_payment_requisites_country_sort",
        "payment_requisites",
        ["country", "sort_order"],
    )


def downgrade() -> None:
    op.drop_index("ix_payment_requisites_country_sort", table_name="payment_requisites")
    for name, _ in reversed(COLUMNS):
        op.drop_column("payment_requisites", name)
