"""Поля под ТЗ от 01.09.2026: плашка ожидания, текст счёта, чек, аккаунт карточки

Все колонки nullable — миграция накатывается на непустую базу без блокировок
и без переноса данных.

Revision ID: d47b21c8fa30
Revises: c31a7d4e9b02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d47b21c8fa30"
down_revision: str | None = "c31a7d4e9b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # п. 2.1 — плашка ожидания гаснет, когда менеджер открыл чат
    op.add_column(
        "conversations", sa.Column("awaiting_seen_at", sa.DateTime(timezone=True), nullable=True)
    )

    # п. 4.4 и 4.5 — текст, которым менеджер сопровождает счёт
    op.add_column("deals", sa.Column("intro_text", sa.Text(), nullable=True))

    # п. 6.3 — без чека оплату подтвердить нельзя
    op.add_column("deals", sa.Column("receipt_number", sa.String(64), nullable=True))
    op.add_column("deals", sa.Column("receipt_at", sa.DateTime(timezone=True), nullable=True))

    # п. 5.2 — через какой аккаунт клиент пришёл впервые
    op.add_column("clients", sa.Column("created_via_account_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_clients_created_via_account",
        "clients",
        "telegram_accounts",
        ["created_via_account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_clients_created_via_account", "clients", type_="foreignkey")
    op.drop_column("clients", "created_via_account_id")
    op.drop_column("deals", "receipt_at")
    op.drop_column("deals", "receipt_number")
    op.drop_column("deals", "intro_text")
    op.drop_column("conversations", "awaiting_seen_at")
