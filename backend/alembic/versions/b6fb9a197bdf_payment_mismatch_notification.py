"""notification_kind: добавить payment_mismatch

Провайдер может прислать оплату с суммой, не совпадающей со сделкой — например,
клиент заплатил по ссылке, устаревшей после редактирования сделки. Раньше это
тонуло в логах сервера; теперь руководителю и продавцу приходит уведомление.

Revision ID: b6fb9a197bdf
Revises: e91a4c6d0f37
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b6fb9a197bdf"
down_revision: str | None = "e91a4c6d0f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS 'payment_mismatch'")


def downgrade() -> None:
    # PostgreSQL не умеет удалять значение enum без пересоздания типа целиком —
    # пересоздавать ради отката ради одной строки не стоит: если понадобится
    # откатиться, значение просто останется неиспользуемым.
    pass
