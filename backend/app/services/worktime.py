"""Рабочее время: сколько из промежутка попало в рабочие часы компании.

Зачем. Клиент написал в 23:40, менеджер ответил в 10:05 — календарно это
десять с половиной часов, и такая метрика говорит только о том, что люди спят.
Если компания работает с 10 до 19, честный ответ — двадцать минут ожидания.

Настройка «Учитывать рабочие часы» включает именно этот пересчёт: он влияет
на время ожидания в списке чатов, на плашку просрочки и на среднее время
ответа в статистике. Выключена — считаем календарно, как раньше.

Тот же расчёт продублирован функцией в PostgreSQL (`astra_working_seconds`):
списки и аналитика считают на стороне базы, а проверка сходимости сравнивает
две независимые реализации между собой.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAY = 24 * 60 * 60


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


async def local_zone(db) -> ZoneInfo:  # noqa: ANN001 — AsyncSession, импорт не нужен
    from app.services import settings_service

    return _zone(await settings_service.get_value(db, "timezone"))


async def day_bounds(db, date_from: date, date_to: date) -> tuple[datetime, datetime]:  # noqa: ANN001
    """Границы периода по датам — в часовом поясе организации, не по UTC.

    Иначе продажа в 23:25 по Москве (20:25 UTC) могла бы попасть не в тот
    день — а на границе месяца или квартала не в тот период вовсе. Верхняя
    граница — начало следующего дня в том же поясе: иначе последний день теряется.
    Единственная реализация на весь бэкенд — используется статистикой, списком
    сделок и профильными показателями, чтобы день не «плавал» между экранами.
    """
    zone = await local_zone(db)
    start = datetime.combine(date_from, time.min, tzinfo=zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


async def current_month_bounds(db) -> tuple[datetime, datetime]:  # noqa: ANN001
    """Границы текущего месяца — в часовом поясе организации, не в UTC.

    Тот же принцип, что и в `day_bounds`: в последние часы месяца по Москве
    (21:00–24:00 UTC) `date_trunc('month', now())` в UTC уже посчитал бы это
    следующим месяцем."""
    zone = await local_zone(db)
    local_now = datetime.now(UTC).astimezone(zone)
    start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(
        month=start.month + 1
    )
    return start.astimezone(UTC), end.astimezone(UTC)


def _window_seconds(work_start: time, work_end: time) -> int:
    """Длительность смены. Конец не позже начала — смена переходит через полночь;
    равенство означает круглые сутки, а не нулевую смену."""
    start = work_start.hour * 3600 + work_start.minute * 60
    end = work_end.hour * 3600 + work_end.minute * 60
    if end > start:
        return end - start
    return DAY - (start - end)


def working_seconds(
    ts_from: datetime,
    ts_to: datetime,
    days: list[int],
    work_start: time,
    work_end: time,
    tz_name: str | None = None,
) -> float:
    """Сколько секунд из промежутка пришлось на рабочие часы.

    Дни задаются по ISO: 1 — понедельник, 7 — воскресенье. Смена принадлежит
    тому дню, в который началась, поэтому ночная смена «22:00–06:00» в
    понедельник — это ночь с понедельника на вторник.
    """
    if ts_to <= ts_from or not days:
        return 0.0
    zone = _zone(tz_name)
    local_from = ts_from.astimezone(zone)
    local_to = ts_to.astimezone(zone)
    duration = _window_seconds(work_start, work_end)
    if duration <= 0:
        return 0.0

    total = 0.0
    # Начинаем на сутки раньше: ночная смена предыдущего дня может заходить внутрь.
    day = (local_from - timedelta(days=1)).date()
    last = local_to.date()
    while day <= last:
        if day.isoweekday() in days:
            shift_start = datetime.combine(day, work_start, tzinfo=zone)
            shift_end = shift_start + timedelta(seconds=duration)
            begin = max(shift_start, local_from)
            finish = min(shift_end, local_to)
            if finish > begin:
                total += (finish - begin).total_seconds()
        day += timedelta(days=1)
    return total


class WorkHours:
    """Рабочие часы компании из настроек, приведённые к одному виду.

    Держим отдельным объектом, чтобы и SQL, и Python получали одни и те же
    значения: расхождение настроек между списком чатов и статистикой означало бы
    две разные цифры об одном и том же ожидании.
    """

    __slots__ = ("enabled", "days", "start", "end", "tz")

    def __init__(self, settings: dict) -> None:
        self.enabled = bool(settings.get("working_hours_enabled"))
        raw_days = settings.get("working_days") or []
        self.days = sorted({int(d) for d in raw_days if 1 <= int(d) <= 7})
        self.start = _parse(settings.get("working_hours_start"), time(9, 0))
        self.end = _parse(settings.get("working_hours_end"), time(21, 0))
        self.tz = settings.get("timezone") or "Europe/Moscow"
        # Включённые часы без дней ничего не ограничивают и обнулили бы все метрики.
        if not self.days:
            self.enabled = False

    def seconds_between(self, ts_from: datetime, ts_to: datetime) -> float:
        if not self.enabled:
            return max(0.0, (ts_to - ts_from).total_seconds())
        return working_seconds(ts_from, ts_to, self.days, self.start, self.end, self.tz)

    def sql_params(self) -> dict:
        """Аргументы для функции astra_working_seconds в SQL-запросах."""
        return {
            "wh_days": self.days or [1, 2, 3, 4, 5, 6, 7],
            "wh_start": self.start,
            "wh_end": self.end,
            "wh_tz": self.tz,
        }


def _parse(raw: object, fallback: time) -> time:
    text = str(raw or "").strip()
    if not text:
        return fallback
    try:
        hours, _, minutes = text.partition(":")
        return time(int(hours), int(minutes[:2] or 0))
    except (TypeError, ValueError):
        return fallback


async def load(db) -> WorkHours:  # noqa: ANN001 — AsyncSession, импорт не нужен
    from app.services import settings_service

    return WorkHours(await settings_service.get_all(db))
