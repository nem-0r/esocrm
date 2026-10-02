"""Подтяжка истории не делает лишней работы (docs/17-resource-autoscaling.md §3.3).

Проверяется на подставном клиенте Telethon, без сети и базы:

- диалог, где последнее сообщение старше границы окна, не открывается совсем —
  ни запроса сообщений, ни паузы;
- сообщение, которое CRM уже знает, не доходит до скачивания файла;
- новое сообщение обрабатывается как раньше, пауза после диалога сохраняется.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.gateway import mtproto_provider as mp
from app.gateway.mtproto_provider import MTProtoProvider

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=10)


def _message(message_id: int, days_ago: float) -> SimpleNamespace:
    return SimpleNamespace(id=message_id, date=NOW - timedelta(days=days_ago))


def _dialog(chat_id: int, last_days_ago: float | None, *, bot: bool = False, user: bool = True):
    date = None if last_days_ago is None else NOW - timedelta(days=last_days_ago)
    return SimpleNamespace(
        id=chat_id, is_user=user, date=date, entity=SimpleNamespace(id=chat_id, bot=bot)
    )


class FakeClient:
    def __init__(self, dialogs, messages_by_chat):
        self._dialogs = dialogs
        self._messages = messages_by_chat
        self.opened: list[int] = []

    async def iter_dialogs(self):
        for dialog in self._dialogs:
            yield dialog

    async def iter_messages(self, entity, limit=None):
        self.opened.append(entity.id)
        for message in self._messages.get(entity.id, []):
            yield message


@pytest.fixture
def harness(monkeypatch):
    calls: list[int] = []
    sleeps: list[float] = []

    async def fake_to_inbound(client, message, account_id, *, live, is_edit=False):
        calls.append(message.id)
        return SimpleNamespace(tg_message_id=message.id)

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    known_by_chat: dict[int, set[int]] = {}

    async def fake_known(account_id, chat_id, since):
        return known_by_chat.get(chat_id, set())

    monkeypatch.setattr(mp.mtproto_receive, "to_inbound", fake_to_inbound)
    monkeypatch.setattr(mp, "_known_since", fake_known)
    monkeypatch.setattr(mp.asyncio, "sleep", fake_sleep)
    return SimpleNamespace(calls=calls, sleeps=sleeps, known=known_by_chat, monkeypatch=monkeypatch)


def _run(harness, client):
    provider = MTProtoProvider()

    async def guarded(account):
        return client

    provider._guarded = guarded  # type: ignore[method-assign]

    async def collect():
        return [item async for item in provider.iter_history(SimpleNamespace(id=6), SINCE)]

    return asyncio.run(collect())


def test_old_dialogs_are_not_opened_and_do_not_cost_a_pause(harness):
    client = FakeClient(
        dialogs=[_dialog(1, 2), _dialog(2, 400), _dialog(3, 1000)],
        messages_by_chat={1: [_message(10, 2)], 2: [_message(20, 400)], 3: [_message(30, 1000)]},
    )
    result = _run(harness, client)
    assert client.opened == [1], "диалоги старше окна не должны открываться"
    assert [m.tg_message_id for m in result] == [10]
    assert len(harness.sleeps) == 1, "пауза нужна только после диалога, который открывали"


def test_known_messages_are_skipped_before_media_is_downloaded(harness):
    harness.known[1] = {11, 12}
    client = FakeClient(
        dialogs=[_dialog(1, 1)],
        messages_by_chat={1: [_message(13, 1), _message(12, 2), _message(11, 3), _message(5, 40)]},
    )
    result = _run(harness, client)
    assert harness.calls == [13], "скачивать (to_inbound) нужно только новое сообщение"
    assert [m.tg_message_id for m in result] == [13]


def test_dialog_without_date_is_still_visited(harness):
    client = FakeClient(dialogs=[_dialog(1, None)], messages_by_chat={1: [_message(7, 1)]})
    result = _run(harness, client)
    assert client.opened == [1]
    assert [m.tg_message_id for m in result] == [7]


def test_bots_and_groups_are_skipped(harness):
    client = FakeClient(
        dialogs=[_dialog(1, 1, bot=True), _dialog(2, 1, user=False), _dialog(3, 1)],
        messages_by_chat={1: [_message(1, 1)], 2: [_message(2, 1)], 3: [_message(3, 1)]},
    )
    _run(harness, client)
    assert client.opened == [3]


def test_window_boundary_stops_the_dialog(harness):
    client = FakeClient(
        dialogs=[_dialog(1, 1)],
        messages_by_chat={1: [_message(9, 1), _message(8, 5), _message(7, 30), _message(6, 31)]},
    )
    _run(harness, client)
    assert harness.calls == [9, 8], "после первого сообщения старше границы диалог обрывается"


def test_dialog_older_than_helper():
    assert mp._dialog_older_than(_dialog(1, 20), SINCE) is True
    assert mp._dialog_older_than(_dialog(1, 5), SINCE) is False
    assert mp._dialog_older_than(_dialog(1, None), SINCE) is False
