"""telegram_accounts: добавить proxy_url_enc

Свой прокси на номер (решение D-21) — раньше код читал единственный глобальный
TELEGRAM_PROXY на все аккаунты сразу, что при нескольких живых номерах прямо
противоречит цели прокси (Telegram видел бы десяток аккаунтов с одного IP).

Revision ID: d4f0912ab5e3
Revises: c3d8e7a41f96
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f0912ab5e3"
down_revision: str | None = "c3d8e7a41f96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_accounts", sa.Column("proxy_url_enc", sa.LargeBinary(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("telegram_accounts", "proxy_url_enc")
