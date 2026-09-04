"""График работы сотрудника: хранение недельного повтора и ответ «на смене ли сейчас».

Смена задаётся тремя вещами: дни недели, начало, конец. Этого достаточно для
графиков вида «Пн–Пт, 10:00–19:00» и «Вт, Чт, Сб, 22:00–06:00» — ночная смена
переходит через полночь и принадлежит тому дню, в который началась.

Считаем в часовом поясе организации (настройка `timezone`), а не в UTC и не в
поясе браузера: смена «с 10 до 19» — это московские десять, откуда бы менеджер
ни открыл CRM.
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def parse_time(value: str) -> time:
    """«10:00» → time(10, 0). Формат проверяет схема, здесь только разбор."""
    hours, _, minutes = value.partition(":")
    return time(int(hours), int(minutes))


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        # Настройку могли испортить руками — смена не должна из-за этого падать.
        return ZoneInfo("Europe/Moscow")


def is_on_shift(
    days: list[int] | None,
    start: time | None,
    end: time | None,
    tz_name: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Идёт ли смена прямо сейчас.

    Ночная смена (конец меньше начала) продолжается в следующие сутки, поэтому
    в 02:00 воскресенья человек всё ещё на субботней смене.
    """
    if not days or start is None or end is None:
        return False
    local = (now or datetime.now(UTC)).astimezone(zone(tz_name))
    minutes = local.hour * 60 + local.minute
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    today = local.isoweekday()

    if start_min < end_min:
        return today in days and start_min <= minutes < end_min
    if start_min == end_min:
        # Круглые сутки: смена идёт весь день, который отмечен в графике.
        return today in days
    # Через полночь: либо хвост вчерашней смены, либо начало сегодняшней.
    yesterday = 7 if today == 1 else today - 1
    return (today in days and minutes >= start_min) or (
        yesterday in days and minutes < end_min
    )


def describe(days: list[int] | None, start: time | None, end: time | None) -> str:
    """Короткая подпись для списка: «Пн–Пт · 10:00–19:00»."""
    if not days or start is None or end is None:
        return "График не задан"
    ordered = sorted(set(days))
    # Идущие подряд дни схлопываем в диапазон: «Пн–Пт» читается быстрее, чем «Пн, Вт, Ср, Чт, Пт».
    groups: list[list[int]] = []
    for day in ordered:
        if groups and day == groups[-1][-1] + 1:
            groups[-1].append(day)
        else:
            groups.append([day])
    parts = [
        DAY_NAMES[g[0] - 1] if len(g) == 1 else f"{DAY_NAMES[g[0] - 1]}–{DAY_NAMES[g[-1] - 1]}"
        for g in groups
    ]
    return f"{', '.join(parts)} · {start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
