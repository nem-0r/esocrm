import { ChevronDown, TrendingDown, TrendingUp } from 'lucide-react'
import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { Granularity } from '@/entities/types'
import { useAuth } from '@/shared/hooks/useAuth'
import { cn } from '@/shared/lib/cn'
import { dateFull, dateShort, daysAgo, durationLabel, isoDate, money } from '@/shared/lib/format'
import {
  Avatar,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  ListSkeleton,
  SectionTitle,
  Segmented,
  Sheet,
  StatTile,
} from '@/shared/ui'
import {
  MAX_DAYS_FOR_DAILY,
  daysBetween,
  useManagerStats,
  useOverview,
  useSeries,
} from '@/features/stats/queries'

// Recharts не понимает классы Tailwind — цвета повторяют токены accent и line.
const BAR_COLOR = '#2F6BE6'
const GRID_COLOR = '#232937'
const AXIS_COLOR = '#6B7385'

type Preset = 'week' | 'month' | 'quarter' | 'custom'

function presetRange(preset: Preset): { from: string; to: string } {
  const to = isoDate(new Date())
  if (preset === 'week') return { from: daysAgo(6), to }
  if (preset === 'quarter') return { from: daysAgo(89), to }
  const now = new Date()
  return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), to }
}

export function StatsPage() {
  const { isAdmin } = useAuth()
  const [preset, setPreset] = useState<Preset>('month')
  const [range, setRange] = useState(() => presetRange('month'))
  const [granularity, setGranularity] = useState<Granularity>('day')
  const [periodOpen, setPeriodOpen] = useState(false)
  const [explainOpen, setExplainOpen] = useState(false)

  const span = daysBetween(range.from, range.to)
  const dayDisabled = span > MAX_DAYS_FOR_DAILY
  const effectiveGranularity: Granularity = dayDisabled && granularity === 'day' ? 'week' : granularity

  const overview = useOverview(range)
  const series = useSeries(range, effectiveGranularity)
  const managers = useManagerStats(range, isAdmin)

  function choose(next: Preset) {
    setPreset(next)
    if (next === 'custom') {
      setPeriodOpen(true)
      return
    }
    setRange(presetRange(next))
  }

  // ТЗ Б.14: при недельной гранулярности подпись «24 авг» непонятна — это день
  // или неделя? Показываем диапазон: «24–30 авг». Месяц пишем один раз, если
  // неделя внутри одного месяца.
  //
  // Границы берём с сервера, а не считаем как «начало + 6 дней»: крайние корзины
  // почти всегда обрезаны периодом. При периоде с 15 июля неделя начинается 13-го,
  // но 13 и 14 июля в столбик не вошли, и подпись «13–19 июл» врала бы на сумму
  // тех двух дней. Честная подпись — «15–19 июл».
  function pointLabel(point: { period_start: string; period_end: string }): string {
    const start = new Date(`${point.period_start}T00:00:00`)
    const end = new Date(`${point.period_end}T00:00:00`)
    if (point.period_start === point.period_end) return dateShort(`${point.period_start}T00:00:00`)
    const from =
      start.getMonth() === end.getMonth()
        ? String(start.getDate())
        : dateShort(`${point.period_start}T00:00:00`)
    return `${from}–${dateShort(`${point.period_end}T00:00:00`)}`
  }

  const points = (series.data?.points ?? []).map((point) => ({
    ...point,
    label: pointLabel(point),
    rubles: Math.round(point.amount / 100),
  }))

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-line px-4 py-3 desk:px-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-2">
          <h1 className="text-lg font-semibold text-ink">Статистика</h1>
          <span className="text-xs text-ink-faint">
            {dateFull(`${range.from}T00:00:00`)} — {dateFull(`${range.to}T00:00:00`)}
          </span>
          <Segmented
            value={preset}
            onChange={choose}
            options={[
              { value: 'week', label: 'Неделя' },
              { value: 'month', label: 'Месяц' },
              { value: 'quarter', label: 'Квартал' },
              { value: 'custom', label: 'Свой' },
            ]}
          />
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4 desk:px-6">
        <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
          {overview.isLoading ? (
            <ListSkeleton rows={3} />
          ) : overview.error ? (
            <ErrorState error={overview.error} onRetry={() => void overview.refetch()} />
          ) : overview.data ? (
            <>
              <div className="grid grid-cols-2 gap-2">
                <StatTile
                  value={money(overview.data.sales_amount)}
                  label="сумма продаж"
                  tone="success"
                  delta={<Delta percent={overview.data.sales_amount_delta_pct} />}
                />
                <StatTile
                  value={overview.data.sales_count}
                  label="продаж"
                  delta={<Delta count={overview.data.sales_count_delta} />}
                />
              </div>

              <Card>
                <SectionTitle
                  action={
                    <Segmented
                      value={effectiveGranularity}
                      onChange={setGranularity}
                      options={[
                        { value: 'day', label: 'Дни' },
                        { value: 'week', label: 'Недели' },
                        { value: 'month', label: 'Месяцы' },
                      ]}
                    />
                  }
                >
                  Продажи
                </SectionTitle>

                {dayDisabled && (
                  <p className="text-micro text-ink-faint">
                    При периоде больше двух месяцев дни недоступны — показаны недели.
                  </p>
                )}

                {series.isLoading ? (
                  <ListSkeleton rows={3} />
                ) : series.error ? (
                  <ErrorState error={series.error} onRetry={() => void series.refetch()} />
                ) : points.every((point) => point.count === 0) ? (
                  <EmptyState title="За этот период продаж не было" />
                ) : (
                  <div className="-mx-2 overflow-x-auto">
                    <div style={{ minWidth: Math.max(320, points.length * 26) }}>
                      <ResponsiveContainer width="100%" height={220}>
                        <BarChart data={points} margin={{ top: 8, right: 8, bottom: 4, left: -8 }}>
                          <CartesianGrid stroke={GRID_COLOR} vertical={false} />
                          <XAxis
                            dataKey="label"
                            stroke={AXIS_COLOR}
                            tick={{ fontSize: 11 }}
                            tickLine={false}
                            axisLine={false}
                            interval="preserveStartEnd"
                            minTickGap={16}
                          />
                          <YAxis
                            stroke={AXIS_COLOR}
                            tick={{ fontSize: 11 }}
                            tickLine={false}
                            axisLine={false}
                            width={56}
                            tickFormatter={(value: number) => value.toLocaleString('ru-RU')}
                          />
                          <Tooltip
                            cursor={{ fill: 'rgba(47,107,230,.12)' }}
                            contentStyle={{
                              background: '#141821',
                              border: `1px solid ${GRID_COLOR}`,
                              borderRadius: 8,
                              fontSize: 12,
                            }}
                            labelStyle={{ color: '#A0A8B8' }}
                            formatter={(_value, _name, item) => {
                              const point = item?.payload as
                                | { amount: number; count: number }
                                | undefined
                              if (!point) return ['—', 'Продажи']
                              return [`${money(point.amount)} · ${point.count} продаж`, 'Продажи']
                            }}
                          />
                          <Bar dataKey="rubles" fill={BAR_COLOR} radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}
              </Card>

              <div className="grid grid-cols-2 gap-2 desk:grid-cols-3">
                <StatTile
                  value={durationLabel(overview.data.avg_response_seconds)}
                  label={`среднее время ответа · цель до ${overview.data.response_goal_minutes} мин`}
                  tone={
                    overview.data.avg_response_seconds !== null &&
                    overview.data.avg_response_seconds <= overview.data.response_goal_minutes * 60
                      ? 'success'
                      : 'warning'
                  }
                />
                <StatTile value={overview.data.active_conversations} label="чатов в работе" />
                <StatTile value={overview.data.new_clients} label="новых клиентов" />
                <StatTile
                  value={money(overview.data.awaiting_amount)}
                  label="ждут оплаты · на сегодня"
                  tone="accent"
                />
                <StatTile
                  value={overview.data.awaiting_count}
                  label="сделок ждут оплаты · на сегодня"
                />
              </div>
            </>
          ) : null}

          {isAdmin && (
            <Card>
              <SectionTitle>По менеджерам</SectionTitle>
              {managers.isLoading ? (
                <ListSkeleton rows={3} />
              ) : managers.error ? (
                <ErrorState error={managers.error} onRetry={() => void managers.refetch()} />
              ) : (managers.data ?? []).length === 0 ? (
                <EmptyState title="Менеджеров пока нет" />
              ) : (
                <ul className="flex flex-col gap-2">
                  {(managers.data ?? []).map((row) => (
                    <li
                      key={row.user.id}
                      className="flex items-center gap-3 rounded-md bg-surface-raised px-3 py-2.5"
                    >
                      <Avatar
                        name={row.user.full_name}
                        color={row.user.avatar_color}
                        size="sm"
                      />
                      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="truncate text-sm text-ink">{row.user.full_name}</span>
                        <span className="text-micro text-ink-faint">
                          {row.sales_count} продаж · ответ {durationLabel(row.avg_response_seconds)} ·{' '}
                          {row.active_conversations} чатов · {row.awaiting_count} ждут оплаты
                        </span>
                      </div>
                      <span className="tnum shrink-0 text-sm font-semibold text-ink">
                        {money(row.sales_amount)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          )}

          <Card>
            <button
              onClick={() => setExplainOpen((v) => !v)}
              className="-my-1 flex min-h-9 items-center justify-between gap-2 py-1 text-left"
            >
              <span className="text-sm text-ink">Как считаются показатели</span>
              <ChevronDown
                className={cn('size-4 text-ink-faint transition-transform', explainOpen && 'rotate-180')}
                aria-hidden
              />
            </button>
            {explainOpen && (
              <ul className="flex flex-col gap-2 text-xs leading-relaxed text-ink-muted">
                <li>
                  <b className="text-ink">Сумма и количество продаж</b> — сделки в статусе
                  «оплачена», по дате оплаты, а не по дате создания.
                </li>
                <li>
                  <b className="text-ink">Прирост</b> — сравнение с предыдущим периодом такой же
                  длины. Если сравнивать не с чем, показывается прочерк.
                </li>
                <li>
                  <b className="text-ink">Среднее время ответа</b> — от входящего, начавшего
                  ожидание, до первого исходящего. Подряд идущие сообщения клиента считаются одним
                  ожиданием. Служебные заметки не считаются ответом.
                </li>
                <li>
                  <b className="text-ink">Чатов в работе</b> — диалоги, где было хотя бы одно
                  сообщение за период.
                </li>
                <li>
                  <b className="text-ink">Новых клиентов</b> — те, у кого первое обращение попало
                  в период.
                </li>
                <li>
                  <b className="text-ink">Ждут оплаты</b> — сколько сейчас не оплачено, всегда на
                  сегодня. Фильтр периода на эту цифру не влияет: сделка, отправленная давно,
                  всё ещё ждёт оплаты сейчас, и прятать её за датами было бы обманом.
                </li>
                <li>
                  <b className="text-ink">Продажа засчитывается</b> тому, кто создал оплату. Снятие
                  менеджера с аккаунта прошлую статистику не меняет.
                </li>
              </ul>
            )}
          </Card>
        </div>
      </div>

      <Sheet
        open={periodOpen}
        onOpenChange={setPeriodOpen}
        title="Период"
        description="Действует на показатели периода. «Ждут оплаты» — исключение, это всегда на сегодня"
        footer={
          <Button fullWidth onClick={() => setPeriodOpen(false)}>
            Применить
          </Button>
        }
      >
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="С">
              <Input
                type="date"
                value={range.from}
                max={range.to}
                onChange={(event) => setRange((prev) => ({ ...prev, from: event.target.value }))}
              />
            </Field>
            <Field label="По">
              <Input
                type="date"
                value={range.to}
                min={range.from}
                onChange={(event) => setRange((prev) => ({ ...prev, to: event.target.value }))}
              />
            </Field>
          </div>
          <Field label="Гранулярность графика" group>
            <Segmented
              value={effectiveGranularity}
              onChange={setGranularity}
              options={[
                { value: 'day', label: 'Дни' },
                { value: 'week', label: 'Недели' },
                { value: 'month', label: 'Месяцы' },
              ]}
            />
          </Field>
          <p className="text-xs text-ink-faint">
            Выбрано {span + 1} дней.{' '}
            {span > MAX_DAYS_FOR_DAILY
              ? 'При периоде больше двух месяцев дни недоступны.'
              : 'Гранулярность можно изменить и в разделе статистики.'}
          </p>
        </div>
      </Sheet>
    </div>
  )
}

function Delta({ percent, count }: { percent?: number | null; count?: number | null }) {
  const value = percent ?? count
  if (value === null || value === undefined) return null
  const positive = value >= 0
  const Icon = positive ? TrendingUp : TrendingDown
  return (
    <span
      className={cn(
        'tnum flex items-center gap-1 text-micro',
        positive ? 'text-success' : 'text-danger',
      )}
    >
      <Icon className="size-3" aria-hidden />
      {positive ? '+' : ''}
      {percent !== undefined && percent !== null ? `${value}%` : value}
    </span>
  )
}
