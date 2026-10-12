"""Связь аккаунта с Telegram: можно ли сейчас отправлять сообщения из CRM (D-37).

Отправлять можно только с подключённого аккаунта. Остальные три состояния —
сессия потеряна (`error`), аккаунт отключён (`disconnected`) и вход не
завершён или сессия отозвана (`pending`) — означают одно: у шлюза нет рабочей
сессии, и сообщение дойдёт до очереди, но не до клиента. Раньше менеджер узнавал
об этом через ~35 секунд и три попытки отправки; теперь отказ приходит сразу.

Модуль чистый: ни базы, ни сети — чтобы правила были проверяемы без стенда.
"""

from app.core.errors import Conflict
from app.models import AccountStatus

NO_LINK_STATUSES: frozenset[AccountStatus] = frozenset(
    {AccountStatus.ERROR, AccountStatus.DISCONNECTED, AccountStatus.PENDING}
)

NO_LINK_CODE = "account_offline"
# Менеджеру остаётся только сообщить, а руководитель может сам переподключить аккаунт.
NO_LINK_TEXT = "Аккаунт без связи — сообщение не отправлено. Сообщите руководителю"
NO_LINK_TEXT_ADMIN = (
    "Аккаунт без связи — сообщение не отправлено. "
    "Переподключите аккаунт в разделе «Профиль → Аккаунты»"
)


def has_link(status: AccountStatus) -> bool:
    return status not in NO_LINK_STATUSES


def no_link_error(account_id: int, status: AccountStatus, *, for_admin: bool = False) -> Conflict:
    """Отказ отправки. По коду интерфейс отличает его от прочих конфликтов."""
    return Conflict(
        NO_LINK_TEXT_ADMIN if for_admin else NO_LINK_TEXT,
        code=NO_LINK_CODE,
        account_id=account_id,
        account_status=status.value,
    )


def entered_error(previous: AccountStatus | None, new: AccountStatus) -> bool:
    """Аккаунт только что потерял связь: был не в `error`, стал в `error`.

    Повторная запись той же ошибки (шлюз сообщает о ней при каждой неудачной
    отправке) — не новый обрыв, и уведомлять о ней второй раз незачем.
    """
    return new == AccountStatus.ERROR and previous != AccountStatus.ERROR
