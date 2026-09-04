"""Номер аккаунта уникален среди живых, а не среди всех когда-либо заведённых

Отключённый аккаунт остаётся в базе — на нём висит переписка, сделки и журнал.
Но его номер продолжал считаться занятым, и подключить тот же номер заново
было нельзя: в списке аккаунта нет, а система говорит «уже подключён».
История при этом сохраняется: новая строка, старая остаётся с пометкой удаления.

Revision ID: d2b6f9c0a153
Revises: c94e2a7b1f58
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2b6f9c0a153"
down_revision: str | None = "c94e2a7b1f58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_telegram_accounts_phone", "telegram_accounts", type_="unique")
    op.create_index(
        "uq_telegram_accounts_phone_alive",
        "telegram_accounts",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_telegram_accounts_phone_alive", table_name="telegram_accounts")
    op.create_unique_constraint("uq_telegram_accounts_phone", "telegram_accounts", ["phone"])
