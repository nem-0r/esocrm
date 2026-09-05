"""notification_kind: добавить payment_orphaned

Провайдер может прислать оплату по сделке, которая уже не в статусе, допускающем
оплату (отменили в момент оплаты, черновик) — деньги у провайдера, а без этого
уведомления никто в CRM об этом не узнал бы.

Revision ID: c3d8e7a41f96
Revises: b6fb9a197bdf
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d8e7a41f96"
down_revision: str | None = "b6fb9a197bdf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS 'payment_orphaned'")


def downgrade() -> None:
    # PostgreSQL не умеет удалять значение enum без пересоздания типа целиком —
    # пересоздавать ради отката ради одной строки не стоит: если понадобится
    # откатиться, значение просто останется неиспользуемым.
    pass
