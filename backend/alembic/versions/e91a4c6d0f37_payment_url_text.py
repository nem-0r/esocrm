"""payment_url: VARCHAR(500) → TEXT

Ссылка на оплату Робокассы содержит URL-кодированный чек по 54-ФЗ — с
кириллическими названиями услуг это легко превышает 500 символов. Найдено
живым запросом при первом создании сделки по ссылке: обрезка строки ронялась
ошибкой базы на середине запроса, а не молча теряла хвост, что не лучше.

Revision ID: e91a4c6d0f37
Revises: d2b6f9c0a153
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e91a4c6d0f37"
down_revision: str | None = "d2b6f9c0a153"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("deals", "payment_url", type_=sa.Text(), existing_nullable=True)


def downgrade() -> None:
    op.alter_column(
        "deals", "payment_url", type_=sa.String(500), existing_nullable=True
    )
