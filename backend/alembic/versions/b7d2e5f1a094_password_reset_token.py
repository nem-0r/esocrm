"""сброс пароля руководителем: одноразовая ссылка на 24 часа (D-38)

Только расширяющая миграция (D-28): три новых колонки допускают NULL, ничего не
удаляется и не меняется. Откат кода на прошлую версию после выкатки безопасен —
старый код новых колонок просто не видит и читает таблицу как раньше.

Ссылка сброса хранится отдельно от приглашения (`invite_*`): приглашение нужно
тому, кто ещё не заходил, и запрещено принявшим; сброс — наоборот, только для
принявших. Общие колонки смешали бы два разных состояния сотрудника.

- `password_reset_token_hash` — SHA-256 токена, как у приглашений; сам токен в
  базе не лежит и после выдачи руководителю больше нигде не показывается;
- `password_reset_expires_at` — до какого момента ссылка действует (24 часа);
- `password_reset_issued_at` — когда выдана последняя ссылка (остаётся после
  использования как след «сброс выдавали»; активной ссылка считается, пока
  заполнен хеш).

Revision ID: b7d2e5f1a094
Revises: 9448414132fb
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7d2e5f1a094"
down_revision: str | None = "9448414132fb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_reset_token_hash", sa.String(255), nullable=True))
    op.add_column(
        "users", sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("password_reset_issued_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Поиск пользователя по хешу токена — как ix_users_invite_token_hash.
    op.create_index("ix_users_password_reset_token_hash", "users", ["password_reset_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_users_password_reset_token_hash", table_name="users")
    op.drop_column("users", "password_reset_issued_at")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_token_hash")
