import { ChevronDown } from 'lucide-react'
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
import { dateFull, dateShort, daysAgo, isoDate, money, plural } from '@/shared/lib/format'
import {
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
} from '@/shared/ui'
import {
  AccountsTable,
  ManagersTable,
  ServicesTable,
} from '@/features/stats/components/BreakdownTables'
import {
  ClientTiles,
  SalesTiles,
  SpeedTiles,
} from '@/features/stats/components/OverviewSections'
import {
  MAX_DAYS_FOR_DAILY,
  daysBetween,
  useAccountStats,
  useManagerStats,
  useOverview,
  useSeries,
  useServiceStats,
} from '@/features/stats/queries'

// Recharts не понимает классы Tailwind — цвета повторяют токены accent и line.
const BAR_COLOR = '#2F6BE6'
const GRID_COLOR = '#232937'
const AXIS_COLOR = '#6B7385'

type Preset = 'week' | 'month' | 'quarter' | 'custom'
type Range = { from: string; to: string }

function presetRange(preset: Preset): Range {
  const to = isoDate(new Date())
  if (preset === 'week') return { from: daysAgo(6), to }
  if (preset === 'quarter') return { from: daysAgo(89), to }
  const now = new Date()
  return { from: isoDate(new Date(now.getFullYear(), now.getMonth(), 1)), to }
}

/**
 * Гранулярность графика («Дни/Недели/Месяцы»). Свой переключатель, а не общий
 * `Segmented`: тому нечем отключить один вариант — а «Дни» обязана быть
 * неактивной на длинном периоде, иначе кнопка нажимается впустую: сервер её
 * не примет, а `useSeries`/`useOverview` сами молча понижают гранулярность
 * до недель (аудит №35).
 */
function GranularityPicker({
  value,
  onChange,
  dayDisabled,
}: {
  value: Granularity
  onChange: (value: Granularity) => void
  dayDisabled: boolean
}) {
  const options: { value: Granularity; label: string }[] = [
    { value: 'day', label: 'Дни' },
    { value: 'week', label: 'Недели' },
    { value: 'month', label: 'Месяцы' },
  ]
  return (
    <div className="flex gap-1.5" role="tablist">
      {options.map((option) => {
        const disabled = option.value === 'day' && dayDisabled
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            aria-disabled={disabled}
            disabled={disabled}
            title={disabled ? 'При периоде больше двух месяцев дни недоступны' : undefined}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex shrink-0 items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors',
              disabled
                ? 'cursor-not-allowed text-ink-faint/50'
                : active
                  ? 'bg-accent-soft text-accent-text'
                  : 'text-ink-muted hover:bg-surface-raised hover:text-ink',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

export function StatsPage() {
  const { isAdmin } = useAuth()
  const [preset, setPreset] = useState<Preset>('month')
  const [range, setRange] = useState(() => presetRange('month'))
  const [granularity, setGranularity] = useState<Granularity>('day')
  const [periodOpen, setPeriodOpen] = useState(false)
  const [explainOpen, setExplainOpen] = useState(false)
  // Черновик окна «Период»: даты и гранулярность внутри него применяются по
  // кнопке «Применить», а не сразу по мере ввода (аудит №35) — иначе кнопка
  // ничего не делает, кроме закрытия окна, а показатели дёргаются на каждую
  // недопечатанную дату. Синхронизируется с боевыми значениями при открытии.
  const [draftRange, setDraftRange] = useState<Range>(range)
  const [draftGranularity, setDraftGranularity] = useState<Granularity>(granularity)

  const span = daysBetween(range.from, range.to)
  const dayDisabled = span > MAX_DAYS_FOR_DAILY
  const effectiveGranularity: Granularity = dayDisabled && granularity === 'day' ? 'week' : granularity
  // Начало позже конца или пустая дата — «Применить» нечего применять: сервер такой
  // период всё равно отклонит, а в окне было бы написано «выбрано −30 дней».
  const draftInvalid = !draftRange.from || !draftRange.to || draftRange.from > draftRange.to
  const draftSpan = daysBetween(draftRange.from, draftRange.to)
  const draftDayDisabled = draftSpan > MAX_DAYS_FOR_DAILY
  // Та же подстраховка, что и у боевой гранулярности: если черновик дат
  // расширили за порог дневного графика уже ПОСЛЕ выбора «Дни», перед
  // применением тихо понижаем до недель — «Применить» не обязан спорить
  // с пользователем, но и не обязан отправить заведомо отклонённый запрос.
  const effectiveDraftGranularity: Granularity =
    draftDayDisabled && draftGranularity === 'day' ? 'week' : draftGranularity

  const overview = useOverview(range)
  const series = useSeries(range, effectiveGranularity)
  const managers = useManagerStats(range, isAdmin)
  const services = useServiceStats(range)
  const accounts = useAccountStats(range)

  function choose(next: Preset) {
    setPreset(next)
    if (next === 'custom') {
      setDraftRange(range)
      setDraftGranularity(effectiveGranularity)
      setPeriodOpen(true)
      return
    }
    setRange(presetRange(next))
  }

  function applyPeriod() {
    if (draftInvalid) return
    setRange(draftRange)
    setGranularity(effectiveDraftGranularity)
    setPeriodOpen(false)
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

  // «Как считаются показатели» называет эти минуты по имени, а не общими
  // словами (аудит №36) — берём их с сервера (настройки руководителя), а не
  // хардкодим: на живой базе они могут отличаться от значений по умолчанию.
  // 15 и 30 — те же значения по умолчанию, что и на сервере (settings_service),
  // только пока сводка ещё не загрузилась.
  const goalMinutes = overview.data?.response_goal_minutes ?? 15
  const lateMinutes = overview.data?.late_threshold_minutes ?? 30

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
              <SalesTiles data={overview.data} />

              <Card>
                <SectionTitle
                  action={
                    <GranularityPicker
                      value={effectiveGranularity}
                      onChange={setGranularity}
                      dayDisabled={dayDisabled}
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
                              return [
                                `${money(point.amount)} · ${point.count} ${plural(point.count, 'продажа', 'продажи', 'продаж')}`,
                                'Продажи',
                              ]
                            }}
                          />
                          <Bar dataKey="rubles" fill={BAR_COLOR} radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                )}
              </Card>

              <ClientTiles data={overview.data} />
              <SpeedTiles data={overview.data} />
            </>
          ) : null}

          <ServicesTable query={services} />
          <AccountsTable query={accounts} />
          {isAdmin && <ManagersTable query={managers} />}

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
                  <b className="text-ink">Среднее время ответа</b> — от сообщения клиента, начавшего
                  ожидание, до первого ответа на него. Несколько сообщений клиента подряд — одно
                  ожидание. Служебные заметки ответом не считаются.
                </li>
                <li>
                  <b className="text-ink">Чатов в работе</b> — диалоги, где было хотя бы одно
                  сообщение за период. Здесь считаются и диалоги без ответственного —
                  в разрезе «По менеджерам» их нет ни у кого, поэтому сумма по строкам
                  бывает меньше этой цифры.
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
                  <b className="text-ink">Средний чек</b> — сумма продаж, делённая на их число.
                </li>
                <li>
                  <b className="text-ink">Конверсия счетов</b> — из сделок, отправленных клиенту в
                  периоде, доля оплаченных на сегодня. Счёт, выставленный в конце периода, может
                  быть оплачен позже — цифра со временем растёт.
                </li>
                <li>
                  <b className="text-ink">Конверсия новых клиентов</b> — из клиентов, впервые
                  написавших в периоде, доля тех, у кого есть оплата.
                </li>
                <li>
                  <b className="text-ink">Ответили вовремя</b> — клиент получил ответ не позже чем
                  через {goalMinutes} мин. Свежие ожидания, у которых это время ещё не истекло, пока
                  не считаются ни вовремя, ни просрочкой.
                </li>
                <li>
                  <b className="text-ink">Просрочки</b> — клиент ждал ответа дольше {lateMinutes} мин,
                  либо до сих пор ждёт дольше этого времени.
                </li>
                <li>
                  <b className="text-ink">Ждут ответа сейчас</b> — диалоги, где последним написал
                  клиент и ответа ещё нет, прямо сейчас. Отдельно показано, сколько из них ждут
                  дольше {lateMinutes} мин.
                </li>
                <li>
                  <b className="text-ink">По услугам</b> — позиции оплаченных сделок. Услуга из
                  справочника — одна строка, даже если название писали по-разному.
                </li>
                <li>
                  <b className="text-ink">По аккаунтам</b> — продажи относятся к аккаунту чата, в
                  котором выставлен счёт.
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

      {/* Закрытие НЕ через «Применить» (Esc, крестик, клик мимо) отменяет
          черновик дат и гранулярности — боевой период он не трогает: раз
          не нажали «Применить», значит передумали, а не подтвердили правку. */}
      <Sheet
        open={periodOpen}
        onOpenChange={setPeriodOpen}
        title="Период"
        description="Даты и гранулярность здесь применяются по кнопке «Применить». «Ждут оплаты» — исключение, это всегда на сегодня"
        footer={
          <Button fullWidth onClick={applyPeriod} disabled={draftInvalid}>
            Применить
          </Button>
        }
      >
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="С">
              <Input
                type="date"
                value={draftRange.from}
                max={draftRange.to}
                onChange={(event) =>
                  setDraftRange((prev) => ({ ...prev, from: event.target.value }))
                }
              />
            </Field>
            <Field label="По">
              <Input
                type="date"
                value={draftRange.to}
                min={draftRange.from}
                onChange={(event) => setDraftRange((prev) => ({ ...prev, to: event.target.value }))}
              />
            </Field>
          </div>
          <Field label="Гранулярность графика" group>
            <GranularityPicker
              value={effectiveDraftGranularity}
              onChange={setDraftGranularity}
              dayDisabled={draftDayDisabled}
            />
          </Field>
          {draftInvalid ? (
            <p className="text-xs text-danger" role="alert">
              {!draftRange.from || !draftRange.to
                ? 'Укажите обе даты.'
                : 'Начало периода позже его конца — поправьте даты.'}
            </p>
          ) : (
            <p className="text-xs text-ink-faint">
              Выбрано {draftSpan + 1} {plural(draftSpan + 1, 'день', 'дня', 'дней')}.{' '}
              {draftDayDisabled
                ? 'При периоде больше двух месяцев дни недоступны.'
                : 'Гранулярность можно изменить и в разделе статистики.'}
            </p>
          )}
        </div>
      </Sheet>
    </div>
  )
}
