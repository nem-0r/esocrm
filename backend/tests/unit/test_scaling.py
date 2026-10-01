"""Автоматический подбор мощности: процессы api, доля аккаунтов на процесс шлюза,
пул соединений с базой, защита от входа в чужой аккаунт Telegram."""

import asyncio
from types import SimpleNamespace

import pytest

from app.core import db as db_module
from app.core import sizing
from app.core.config import settings
from app.gateway import lease, topology
from app.gateway.mtproto_provider import MTProtoProvider

# ------------------------------------------------------------- процессы api


@pytest.mark.parametrize(
    ("cores", "memory_mb", "expected"),
    [
        (4, 1536, 2),    # нынешний прод: ровно то, что было вписано руками
        (8, 1536, 4),    # апгрейд до 8 ядер — вдвое больше процессов
        (16, 1536, 4),   # упёрлись в потолок памяти контейнера, а не в ядра
        (16, 4096, 8),   # потолок памяти подняли — растём до верхней границы
        (2, 1536, 2),    # маленький сервер: не меньше двух
        (1, 1536, 2),
        (8, 512, 1),     # памяти на один процесс едва хватает
        (64, None, 8),   # потолка памяти нет — только ядра и верхняя граница
    ],
)
def test_api_workers_scale_with_hardware(monkeypatch, cores, memory_mb, expected):
    monkeypatch.delenv("API_WORKERS", raising=False)
    monkeypatch.setattr(sizing, "detected_cores", lambda: cores)
    monkeypatch.setattr(sizing, "memory_limit_mb", lambda: memory_mb)
    assert sizing.api_workers() == expected


def test_api_workers_explicit_override(monkeypatch):
    monkeypatch.setenv("API_WORKERS", "3")
    assert sizing.api_workers() == 3
    monkeypatch.setenv("API_WORKERS", "0")  # мусор — считаем по железу
    monkeypatch.setattr(sizing, "detected_cores", lambda: 4)
    monkeypatch.setattr(sizing, "memory_limit_mb", lambda: 1536)
    assert sizing.api_workers() == 2


def test_api_pool_budget_is_divided_between_workers(monkeypatch):
    """Соединений с базой суммарно не больше, сколько бы процессов ни стало."""
    monkeypatch.setattr(settings, "service_role", "api")
    totals = {}
    for workers in (2, 4, 8):
        monkeypatch.setenv("API_WORKERS_RESOLVED", str(workers))
        kwargs = db_module._pool_kwargs()
        totals[workers] = workers * (kwargs["pool_size"] + kwargs["max_overflow"])
    monkeypatch.setenv("API_WORKERS_RESOLVED", "2")
    assert db_module._pool_kwargs() == {"pool_size": 10, "max_overflow": 20}  # как было
    assert totals[2] == 60 and totals[4] == 60  # те же 60 соединений на двоих и на четверых
    assert totals[8] == 72  # на восьми — минимум 4 на процесс, чуть выше, но не ×4


# ----------------------------------------------- доля аккаунтов на процесс шлюза


@pytest.mark.parametrize(
    ("accounts", "workers", "expected"),
    [
        (16, 3, [6, 5, 5]),    # а не 16 + 0 + 0
        (10, 3, [4, 3, 3]),
        (12, 3, [4, 4, 4]),
        (13, 4, [4, 3, 3, 3]),
        (1, 3, [1, 0, 0]),     # один аккаунт — его кто-то возьмёт
        (0, 3, [0, 0, 0]),
        (25, 1, [25]),         # один процесс — всё ему
        (45, 3, [15, 15, 15]),
    ],
)
def test_fair_limit_spreads_accounts_across_workers(accounts, workers, expected):
    assert [lease.fair_limit(accounts, workers, rank) for rank in range(workers)] == expected


def test_fair_limit_covers_all_accounts_exactly():
    """Сумма долей — ровно число аккаунтов: ни один не остаётся без хозяина, лишних нет."""
    for accounts in range(0, 80):
        for workers in range(1, 10):
            shares = [lease.fair_limit(accounts, workers, rank) for rank in range(workers)]
            assert sum(shares) == accounts
            assert max(shares) - min(shares) <= 1  # поровну с точностью до одного


def test_fair_limit_without_live_workers_does_not_divide_by_zero():
    assert lease.fair_limit(7, 0, 0) == 7


def test_expected_workers_from_supervisor(monkeypatch):
    monkeypatch.setenv("GATEWAY_EXPECTED_WORKERS", "3")
    assert topology.expected_workers() == 3
    monkeypatch.setenv("GATEWAY_EXPECTED_WORKERS", "abc")
    assert topology.expected_workers() == 1
    monkeypatch.delenv("GATEWAY_EXPECTED_WORKERS")
    assert topology.expected_workers() == 1


# --------------------------------------- переподключение: тот же аккаунт Telegram


class _FakeClient:
    def __init__(self, user_id: int, username: str | None = "someone") -> None:
        self.me = SimpleNamespace(id=user_id, username=username)
        self.logged_out = False
        self.disconnected = False
        self.session = SimpleNamespace(save=lambda: "SESSION")

    def is_connected(self) -> bool:
        return not self.disconnected

    async def sign_in(self, **_kwargs):
        return None

    async def get_me(self):
        return self.me

    async def log_out(self):
        self.logged_out = True

    async def disconnect(self):
        self.disconnected = True


def _provider() -> MTProtoProvider:
    provider = MTProtoProvider()
    provider._register_handlers = lambda *_a, **_k: None  # type: ignore[method-assign]
    return provider


def test_reconnect_with_other_telegram_account_is_rejected():
    provider = _provider()
    client = _FakeClient(user_id=222, username="stranger")
    account = SimpleNamespace(id=6, phone="+79990000000", tg_user_id=111)
    provider._logins[6] = client  # type: ignore[assignment]
    with pytest.raises(ValueError, match=r"другой аккаунт Telegram \(@stranger\)"):
        asyncio.run(provider.confirm_code(account, "12345", "hash"))  # type: ignore[arg-type]
    assert client.logged_out, "чужая сессия должна быть отозвана в Telegram"
    assert 6 not in provider._clients, "чужой клиент не должен стать рабочим"
    assert 6 not in provider._logins


def test_reconnect_with_same_telegram_account_works():
    provider = _provider()
    client = _FakeClient(user_id=111)
    account = SimpleNamespace(id=6, phone="+79990000000", tg_user_id=111)
    provider._logins[6] = client  # type: ignore[assignment]
    result = asyncio.run(provider.confirm_code(account, "12345", "hash"))  # type: ignore[arg-type]
    assert result.session_string == "SESSION"
    assert result.tg_user_id == 111
    assert provider._clients[6] is client
    assert not client.logged_out


def test_first_connection_has_nothing_to_compare():
    provider = _provider()
    client = _FakeClient(user_id=333)
    account = SimpleNamespace(id=9, phone="+79990000001", tg_user_id=None)
    provider._logins[9] = client  # type: ignore[assignment]
    result = asyncio.run(provider.confirm_code(account, "12345", "hash"))  # type: ignore[arg-type]
    assert result.tg_user_id == 333


def test_qr_login_with_other_account_is_rejected():
    provider = _provider()
    client = _FakeClient(user_id=222, username=None)
    session = SimpleNamespace(client=client, status="waiting", message=None, expected_user_id=111)
    with pytest.raises(ValueError, match="id 222"):
        asyncio.run(provider._qr_finish(6, session))  # type: ignore[arg-type]
    assert client.logged_out
    assert 6 not in provider._clients
