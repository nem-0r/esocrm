"""deals: чек оплаты — файл вместо номера, код платежа убран

Два независимых решения одним разом, оба по одной и той же причине —
подтверждение оплаты по реквизитам держалось на честном слове:

1. Код платежа (`payment_code`, показывался клиенту с просьбой указать его
   в комментарии к переводу) убран целиком — колонка, уникальный индекс,
   участие в поиске. Клиенты не проставляли его достаточно надёжно, чтобы
   на него полагаться; сверка теперь идёт по приложенному чеку.
2. `receipt_number`/`receipt_at` (номер чека, который менеджер печатал
   вручную) заменены на сам файл чека: `receipt_storage_key` (ключ в
   объектном хранилище), `receipt_file_name`, `receipt_mime_type`,
   `receipt_size_bytes`.

Revision ID: a4f7c8e21d90
Revises: c3d8e7a41f96
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a4f7c8e21d90"
down_revision: str | None = "c3d8e7a41f96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_deals_payment_code", table_name="deals")
    op.drop_column("deals", "payment_code")
    op.drop_column("deals", "receipt_number")
    op.drop_column("deals", "receipt_at")
    op.add_column("deals", sa.Column("receipt_storage_key", sa.String(500), nullable=True))
    op.add_column("deals", sa.Column("receipt_file_name", sa.String(255), nullable=True))
    op.add_column("deals", sa.Column("receipt_mime_type", sa.String(120), nullable=True))
    op.add_column("deals", sa.Column("receipt_size_bytes", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("deals", "receipt_size_bytes")
    op.drop_column("deals", "receipt_mime_type")
    op.drop_column("deals", "receipt_file_name")
    op.drop_column("deals", "receipt_storage_key")
    op.add_column("deals", sa.Column("receipt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("deals", sa.Column("receipt_number", sa.String(64), nullable=True))
    op.add_column("deals", sa.Column("payment_code", sa.String(32), nullable=True))
    op.create_index(
        "uq_deals_payment_code",
        "deals",
        ["payment_code"],
        unique=True,
        postgresql_where=sa.text("payment_code IS NOT NULL"),
    )
