"""Фильтр «Без аккаунта» в списке сотрудников (замечание L5).

Руководителю рабочий аккаунт не нужен (он и так видит все), отключённому
сотруднику — тем более. В фильтр попадают только активные менеджеры, у которых
нет ни одного живого аккаунта. Условие собирается отдельной функцией, поэтому
его можно проверить без базы; настоящие строки проверяет
`tests/verify_profile_remarks.py`.
"""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import User
from app.services import user_service


def _sql() -> str:
    stmt = select(User.id).where(*user_service.no_account_clauses())
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def test_only_managers_are_listed() -> None:
    assert "users.role = 'manager'" in _sql()


def test_deactivated_staff_are_not_listed() -> None:
    assert "users.is_active IS true" in _sql()


def test_deleted_accounts_do_not_count_as_an_account() -> None:
    """Аккаунт удаляется мягко, а связь с менеджером остаётся.

    В карточке сотрудника такой аккаунт не показан («· без аккаунта»), значит и
    фильтр обязан считать этого человека сотрудником без аккаунта.
    """
    sql = _sql()
    assert "NOT (EXISTS" in sql
    assert "telegram_accounts.deleted_at IS NULL" in sql
    assert "account_managers.user_id = users.id" in sql
