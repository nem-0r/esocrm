"""Поля из модели данных: согласия, приблизительное время рождения, закрытие диалога

Всё добавляется как nullable или со значением по умолчанию, поэтому миграция
накатывается на непустую базу без блокировок и без переноса данных.

Revision ID: c31a7d4e9b02
Revises: b2f56eadabd3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c31a7d4e9b02"
down_revision: str | None = "b2f56eadabd3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIRTH_TIME_APPROX = sa.Enum(
    "morning", "day", "evening", "night", name="birth_time_approx"
)


def upgrade() -> None:
    BIRTH_TIME_APPROX.create(op.get_bind(), checkfirst=True)

    op.add_column("clients", sa.Column("birth_time_approx", BIRTH_TIME_APPROX, nullable=True))
    op.add_column("clients", sa.Column("pdn_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clients", sa.Column("pdn_consent_version", sa.String(20), nullable=True))
    op.add_column(
        "clients",
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "clients", sa.Column("marketing_consent_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.add_column(
        "conversations", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("attachments", sa.Column("telegram_file_id", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("attachments", "telegram_file_id")
    op.drop_column("conversations", "closed_at")
    op.drop_column("clients", "marketing_consent_at")
    op.drop_column("clients", "marketing_consent")
    op.drop_column("clients", "pdn_consent_version")
    op.drop_column("clients", "pdn_consent_at")
    op.drop_column("clients", "birth_time_approx")
    BIRTH_TIME_APPROX.drop(op.get_bind(), checkfirst=True)
