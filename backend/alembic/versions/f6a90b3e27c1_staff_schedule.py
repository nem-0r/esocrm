"""График работы сотрудника: включён, дни недели, начало и конец

Недельный повтор, а не календарь смен. Смены с заменами, отпусками и обменами —
это система учёта рабочего времени, а её в задаче нет. Когда понадобятся
исключения на конкретные даты, здесь появится отдельная таблица.

Revision ID: f6a90b3e27c1
Revises: e58c93df1a44
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f6a90b3e27c1"
down_revision: str | None = "e58c93df1a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("schedule_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Дни недели по ISO: 1 — понедельник, 7 — воскресенье.
    op.add_column("users", sa.Column("work_days", JSONB(), nullable=True))
    op.add_column("users", sa.Column("work_start", sa.Time(), nullable=True))
    op.add_column("users", sa.Column("work_end", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "work_end")
    op.drop_column("users", "work_start")
    op.drop_column("users", "work_days")
    op.drop_column("users", "schedule_enabled")
