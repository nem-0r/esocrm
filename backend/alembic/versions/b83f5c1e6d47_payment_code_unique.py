"""Код платежа: уникальность и подтверждение поступления

Код в комментарии к переводу — единственная нить между строкой банковской
выписки и сделкой, поэтому он обязан быть уникальным. Старые числовые коды
остаются как есть: индекс частичный и на них не ругается.

Revision ID: b83f5c1e6d47
Revises: a71c48d0e5b3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b83f5c1e6d47"
down_revision: str | None = "a71c48d0e5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_deals_payment_code", table_name="deals")
    op.create_index(
        "uq_deals_payment_code",
        "deals",
        ["payment_code"],
        unique=True,
        postgresql_where=sa.text("payment_code IS NOT NULL"),
    )
    # На какой именно реквизит пришли деньги. Может отличаться от того, что
    # отправляли в счёте: клиент из Казахстана нередко платит с российской карты
    # родственника, и при сверке с выпиской важно, куда деньги дошли на самом деле.
    op.add_column("deals", sa.Column("paid_to_requisite_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_deals_paid_to_requisite",
        "deals",
        "payment_requisites",
        ["paid_to_requisite_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_deals_paid_to_requisite", "deals", type_="foreignkey")
    op.drop_column("deals", "paid_to_requisite_id")
    op.drop_index("uq_deals_payment_code", table_name="deals")
    op.create_index("ix_deals_payment_code", "deals", ["payment_code"])
