"""Штамп плана ресурсов в окружении: «план устарел», когда сервер сменил размер."""

import pytest

from app.core import hostinfo
from app.services import system_status


def _set(monkeypatch, *, cores, ram_mb, plan_cores=None, plan_ram=None):
    monkeypatch.setattr(hostinfo, "detected_cores", lambda: cores)
    monkeypatch.setattr(system_status, "_total_memory_mb", lambda: ram_mb)
    for key, value in (("ASTRA_PLAN_CORES", plan_cores), ("ASTRA_PLAN_RAM_MB", plan_ram)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, str(value))


def test_no_plan_yet_is_not_an_alarm(monkeypatch):
    _set(monkeypatch, cores=4, ram_mb=7934)
    info = system_status.plan_info()
    assert info["applied"] is False and info["stale"] is False


def test_plan_matches_server(monkeypatch):
    _set(monkeypatch, cores=4, ram_mb=7934, plan_cores=4, plan_ram=7934)
    assert system_status.plan_info()["stale"] is False


@pytest.mark.parametrize(
    ("cores", "ram"),
    [(6, 16384), (8, 7934), (4, 16384), (2, 7934), (4, 3900)],
)
def test_plan_is_stale_when_server_changed(monkeypatch, cores, ram):
    _set(monkeypatch, cores=cores, ram_mb=ram, plan_cores=4, plan_ram=7934)
    info = system_status.plan_info()
    assert info["stale"] is True and info["plan_cores"] == 4


def test_small_memory_difference_is_not_a_change(monkeypatch):
    """Хост отдаёт на несколько мегабайт меньше после перезагрузки — это не апгрейд."""
    _set(monkeypatch, cores=4, ram_mb=7900, plan_cores=4, plan_ram=7934)
    assert system_status.plan_info()["stale"] is False
