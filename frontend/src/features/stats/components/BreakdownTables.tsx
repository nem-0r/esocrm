import type { AccountStatsRow, ManagerStats, ServiceStats } from '@/entities/types'
import { FUNNEL_LABEL } from '@/features/profile/lib'
import { ShareBar, pct } from '@/features/stats/components/Delta'
import { cn } from '@/shared/lib/cn'
import { durationLabel, money, moneyWhole } from '@/shared/lib/format'
import { Avatar, Card, EmptyState, ErrorState, ListSkeleton, SectionTitle } from '@/shared/ui'

interface QueryLike<T> {
  data?: T
  isLoading: boolean
  error: unknown
  refetch: () => unknown
}

function Frame<T>({
  title,
  hint,
  query,
  empty,
  children,
}: {
  title: string
  hint?: string
  query: QueryLike<T>
  empty: (data: T) => boolean
  children: (data: T) => React.ReactNode
}) {
  return (
    <Card>
      <SectionTitle>{title}</SectionTitle>
      {hint && <p className="-mt-1 text-micro text-ink-faint">{hint}</p>}
      {query.isLoading ? (
        <ListSkeleton rows={3} />
      ) : query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : query.data === undefined || empty(query.data) ? (
        <EmptyState title="За этот период данных нет" />
      ) : (
        children(query.data)
      )}
    </Card>
  )
}

/** Таблица шире экрана на телефоне — прокрутка есть (overflow-x-auto), но на
 *  тачскрине у неё нет видимой полосы, и без прямой подсказки не догадаться,
 *  что можно листать вбок, а не всё уместилось (аудит №37). */
function ScrollHint() {
  return (
    <p className="text-micro text-ink-faint desk:hidden">Таблица шире экрана — листайте вбок →</p>
  )
}

/** Продажи по услугам: сколько, на какую сумму, доля выручки, конверсия выставленного. */
export function ServicesTable({ query }: { query: QueryLike<ServiceStats> }) {
  return (
    <Frame
      title="По услугам"
      hint="Продано и выручка — по дате оплаты. Конверсия — из выставленных в периоде счетов."
      query={query}
      empty={(data) => data.rows.length === 0}
    >
      {(data) => (
        <div className="flex flex-col gap-1.5">
          <ScrollHint />
          <div className="-mx-1 overflow-x-auto">
            <table className="w-full min-w-max border-separate border-spacing-y-1 text-sm">
              <thead>
                <tr className="text-left text-micro uppercase tracking-wide text-ink-faint">
                  <th className="px-2 font-normal">Услуга</th>
                  <th className="px-2 text-right font-normal">Продано</th>
                  <th className="px-2 text-right font-normal">Выручка</th>
                  <th className="w-32 px-2 font-normal">Доля</th>
                  <th className="px-2 text-right font-normal">Конверсия</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.key} className="bg-surface-raised">
                    <td className="rounded-l-md px-2 py-2 text-ink">{row.name}</td>
                    <td className="tnum px-2 py-2 text-right text-ink-muted">{row.sold_count}</td>
                    <td className="tnum px-2 py-2 text-right font-semibold text-ink">
                      {money(row.revenue)}
                    </td>
                    <td className="px-2 py-2">
                      <div className="flex items-center gap-2">
                        <ShareBar value={row.share_pct} />
                        <span className="tnum w-12 shrink-0 text-right text-micro text-ink-muted">
                          {pct(row.share_pct)}
                        </span>
                      </div>
                    </td>
                    <td className="tnum rounded-r-md px-2 py-2 text-right text-ink-muted">
                      {row.offered ? (
                        <span title={`оплачено ${row.offered_paid} из ${row.offered}`}>
                          {pct(row.conversion_pct)}
                        </span>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Frame>
  )
}

/** Разрез по аккаунтам — направлениям воронки (Бот, Первые продажи, Допы). */
export function AccountsTable({ query }: { query: QueryLike<AccountStatsRow[]> }) {
  return (
    <Frame
      title="По аккаунтам"
      hint="Продажи — по аккаунту чата, в котором выставлен счёт."
      query={query}
      empty={(rows) => rows.length === 0}
    >
      {(rows) => (
        <div className="flex flex-col gap-1.5">
          <ScrollHint />
          <div className="-mx-1 overflow-x-auto">
            <table className="w-full min-w-max border-separate border-spacing-y-1 text-sm">
              <thead>
                <tr className="text-left text-micro uppercase tracking-wide text-ink-faint">
                  <th className="px-2 font-normal">Аккаунт</th>
                  <th className="px-2 text-right font-normal">Продажи</th>
                  <th className="px-2 text-right font-normal">Ср. чек</th>
                  <th className="px-2 text-right font-normal">Новых чатов</th>
                  <th className="px-2 text-right font-normal">В работе</th>
                  <th className="px-2 text-right font-normal">Ответ</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.account.id} className="bg-surface-raised">
                    <td className="rounded-l-md px-2 py-2">
                      <span className="block text-ink">{row.account.title}</span>
                      <span className="block text-micro text-ink-faint">
                        {FUNNEL_LABEL[row.account.funnel_stage]}
                      </span>
                    </td>
                    <td className="px-2 py-2 text-right">
                      <span className="tnum block font-semibold text-ink">{money(row.sales_amount)}</span>
                      <span className="tnum block text-micro text-ink-faint">{row.sales_count} шт.</span>
                    </td>
                    <td className="tnum px-2 py-2 text-right text-ink-muted">
                      {moneyWhole(row.avg_check)}
                    </td>
                    <td className="tnum px-2 py-2 text-right text-ink-muted">{row.new_conversations}</td>
                    <td className="tnum px-2 py-2 text-right text-ink-muted">{row.active_conversations}</td>
                    <td className="rounded-r-md px-2 py-2 text-right">
                      <span className="tnum block text-ink-muted">{durationLabel(row.avg_response_seconds)}</span>
                      {row.in_goal_pct !== null && (
                        <span className="tnum block text-micro text-ink-faint">
                          вовремя {pct(row.in_goal_pct)}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Frame>
  )
}

/** Руководителю: продажи, средний чек, конверсия и скорость каждого менеджера. */
export function ManagersTable({ query }: { query: QueryLike<ManagerStats[]> }) {
  return (
    <Frame
      title="По менеджерам"
      hint="Чаты без ответственного менеджера сюда не входят."
      query={query}
      empty={(rows) => rows.length === 0}
    >
      {(rows) => (
        <ul className="flex flex-col gap-2">
          {rows.map((row) => (
            <li key={row.user.id} className="flex flex-col gap-2 rounded-md bg-surface-raised px-3 py-2.5">
              <div className="flex items-center gap-3">
                <Avatar name={row.user.full_name} color={row.user.avatar_color} size="sm" />
                <span className="min-w-0 flex-1 truncate text-sm text-ink">{row.user.full_name}</span>
                <span className="tnum shrink-0 text-sm font-semibold text-ink">
                  {money(row.sales_amount)}
                </span>
              </div>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-micro desk:grid-cols-4 desk:gap-y-3">
                <Metric label="продаж" value={String(row.sales_count)} />
                <Metric label="средний чек" value={moneyWhole(row.avg_check)} />
                <Metric
                  label="счетов оплачено"
                  value={
                    row.invoices_sent
                      ? `${pct(row.invoice_conversion_pct)} · ${row.invoices_paid}/${row.invoices_sent}`
                      : '—'
                  }
                />
                <Metric label="ответ в среднем" value={durationLabel(row.avg_response_seconds)} />
                <Metric label="ответили вовремя" value={pct(row.in_goal_pct)} />
                <Metric label="просрочек" value={String(row.late_count)} warn={row.late_count > 0} />
                <Metric
                  label="ждут ответа сейчас"
                  value={String(row.awaiting_reply_now)}
                  warn={row.awaiting_reply_now > 0}
                />
                <Metric label="чатов в работе" value={String(row.active_conversations)} />
              </dl>
            </li>
          ))}
        </ul>
      )}
    </Frame>
  )
}

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-2 desk:flex-col desk:items-start desk:justify-start desk:gap-0.5">
      <dt className="min-w-0 text-ink-faint">{label}</dt>
      <dd
        className={cn(
          'tnum shrink-0 whitespace-nowrap desk:text-sm',
          warn ? 'text-warning' : 'text-ink-muted desk:text-ink',
        )}
      >
        {value}
      </dd>
    </div>
  )
}

