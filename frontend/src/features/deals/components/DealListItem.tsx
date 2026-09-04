/** Строка списка оплат: клиент, услуга, сумма, статус, дата. */

import { Link } from 'react-router-dom'

import type { DealRow } from '@/entities/types'
import { cn } from '@/shared/lib/cn'
import { dateShort, money } from '@/shared/lib/format'
import { Avatar, Badge } from '@/shared/ui'
import {
  DEAL_STATUS_LABEL,
  DEAL_STATUS_TONE,
  daysWithoutAnswerLabel,
} from '@/features/deals/lib'

export function DealListItem({ deal, active }: { deal: DealRow; active?: boolean }) {
  const silent = deal.status === 'awaiting' && (deal.days_without_answer ?? 0) > 0

  return (
    <Link
      to={`/payments/${deal.id}`}
      className={cn(
        'flex items-start gap-3 border-b border-line px-4 py-3 transition-colors',
        active ? 'bg-accent-soft' : 'hover:bg-surface-raised',
      )}
    >
      <Avatar name={deal.client.name} size="md" className="mt-0.5" />

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-semibold text-ink">{deal.client.name}</span>
          <span className="tnum shrink-0 text-micro text-ink-faint">id {deal.client.id}</span>
          <span className="tnum ml-auto shrink-0 text-sm font-semibold text-ink">
            {money(deal.total_amount)}
          </span>
        </div>

        <div className="flex items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">{deal.title}</span>
          <span className="tnum shrink-0 text-micro text-ink-faint">
            {deal.status === 'paid' ? 'оплачено ' : ''}
            {dateShort(deal.event_at ?? deal.created_at)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={DEAL_STATUS_TONE[deal.status]}>{DEAL_STATUS_LABEL[deal.status]}</Badge>
          <span className="tnum text-micro text-ink-faint">{deal.number}</span>
        </div>

        {silent && (
          <span className="text-micro text-warning">
            {daysWithoutAnswerLabel(deal.days_without_answer ?? 0)}
          </span>
        )}
      </div>
    </Link>
  )
}
