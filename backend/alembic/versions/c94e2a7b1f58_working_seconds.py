"""Функция рабочего времени в базе: astra_working_seconds

Клиент написал в 23:40, ответили в 10:05. Календарно — десять часов, и такая
метрика говорит лишь о том, что люди спят. Если компания работает с 10 до 19,
честный ответ — двадцать минут. Списки и аналитика считают это на стороне базы,
поэтому расчёт нужен здесь, а не только в Python.

Функция помечена immutable: при одних аргументах результат не меняется, и
планировщик может считать её один раз на строку.

Revision ID: c94e2a7b1f58
Revises: b83f5c1e6d47
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c94e2a7b1f58"
down_revision: str | None = "b83f5c1e6d47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CREATE = """
create or replace function astra_working_seconds(
    ts_from timestamptz,
    ts_to timestamptz,
    work_days int[],
    work_start time,
    work_end time,
    tz text
) returns double precision
language plpgsql
immutable
as $$
declare
    zone text := coalesce(nullif(tz, ''), 'Europe/Moscow');
    local_from timestamp;
    local_to timestamp;
    duration interval;
    day date;
    last_day date;
    shift_start timestamp;
    shift_end timestamp;
    total double precision := 0;
begin
    if ts_to <= ts_from or work_days is null or array_length(work_days, 1) is null then
        return 0;
    end if;

    local_from := ts_from at time zone zone;
    local_to := ts_to at time zone zone;

    -- Конец не позже начала — смена переходит через полночь; равенство означает
    -- круглые сутки, а не нулевую смену.
    if work_end > work_start then
        duration := work_end - work_start;
    else
        duration := interval '24 hours' - (work_start - work_end);
    end if;
    if duration <= interval '0' then
        return 0;
    end if;

    -- На сутки раньше: ночная смена предыдущего дня может заходить внутрь промежутка.
    day := (local_from - interval '1 day')::date;
    last_day := local_to::date;

    while day <= last_day loop
        if extract(isodow from day)::int = any(work_days) then
            shift_start := day + work_start;
            shift_end := shift_start + duration;
            if least(shift_end, local_to) > greatest(shift_start, local_from) then
                total := total + extract(
                    epoch from (least(shift_end, local_to) - greatest(shift_start, local_from))
                );
            end if;
        end if;
        day := day + 1;
    end loop;

    return total;
end;
$$;
"""


def upgrade() -> None:
    op.execute(CREATE)


def downgrade() -> None:
    op.execute(
        "drop function if exists astra_working_seconds"
        "(timestamptz, timestamptz, int[], time, time, text)"
    )
