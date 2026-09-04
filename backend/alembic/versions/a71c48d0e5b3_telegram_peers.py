"""Собеседники аккаунта: access_hash, профиль, связь с карточкой клиента

Ключ составной — (аккаунт, пользователь Telegram): пропуск access_hash выдаётся
конкретному аккаунту и у соседнего не работает.

Revision ID: a71c48d0e5b3
Revises: f6a90b3e27c1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a71c48d0e5b3"
down_revision: str | None = "f6a90b3e27c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_peers",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("access_hash", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.String(120), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("first_name", sa.String(255), nullable=True),
        sa.Column("last_name", sa.String(255), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["account_id"], ["telegram_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("account_id", "tg_user_id", name="pk_telegram_peers"),
    )
    op.create_index("ix_telegram_peers_client", "telegram_peers", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_peers_client", table_name="telegram_peers")
    op.drop_table("telegram_peers")
