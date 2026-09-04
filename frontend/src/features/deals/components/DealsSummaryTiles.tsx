/** Три плитки над списком оплат. Считает их сервер: GET /deals/summary. */

import type { DealsSummary } from '@/entities/types'
import { money, plural } from '@/shared/lib/format'
import { InlineError, Skeleton, StatTile } from '@/shared/ui'

function Sub({ children }: { children: string }) {
  return <span className="text-micro leading-snug text-ink-faint">{children}</span>
}

export function DealsSummaryTiles({
  summary,
  isLoading,
  error,
  onRetry,
}: {
  summary: DealsSummary | undefined
  isLoading: boolean
  error: unknown
  onRetry: () => void
}) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-2 desk:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 rounded-lg" />
        ))}
      </div>
    )
  }

  if (error) {
    return <InlineError message="Не удалось посчитать итоги за период" onRetry={onRetry} />
  }

  if (!summary) return null

  return (
    <div className="grid grid-cols-2 gap-2 desk:grid-cols-3">
      <StatTile
        tone="success"
        value={money(summary.paid_amount)}
        label="оплачено"
        delta={
          <Sub>
            {`${summary.paid_count} ${plural(summary.paid_count, 'сделка', 'сделки', 'сделок')}`}
          </Sub>
        }
      />
      <StatTile
        tone="accent"
        value={money(summary.awaiting_amount)}
        label="ждут оплаты · на сегодня"
        delta={
          <Sub>
            {`${summary.awaiting_count} ${plural(summary.awaiting_count, 'сделка', 'сделки', 'сделок')}`}
          </Sub>
        }
      />
      <StatTile
        value={summary.clients_with_deals}
        label="клиентов с оплатами"
        delta={<Sub>за выбранный период</Sub>}
      />
    </div>
  )
}
