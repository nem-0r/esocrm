import { Link } from 'react-router-dom'

import type { ClientRow } from '@/entities/types'
import { cn } from '@/shared/lib/cn'
import { listTime } from '@/shared/lib/format'
import { Avatar, Badge } from '@/shared/ui'
import { birthSummary, purchasesLabel } from '@/features/clients/clientText'

export function ClientListItem({ client, active }: { client: ClientRow; active?: boolean }) {
  const birth = birthSummary(client)

  return (
    <Link
      to={`/clients/${client.id}`}
      className={cn(
        'flex items-start gap-3 border-b border-line px-4 py-3 transition-colors',
        active ? 'bg-accent-soft' : 'hover:bg-surface-raised',
      )}
    >
      <Avatar name={client.name} size="md" className="mt-0.5" />

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate text-sm font-semibold text-ink">{client.name}</span>
          <span className="tnum shrink-0 text-micro text-ink-faint">id {client.id}</span>
          <span className="tnum ml-auto shrink-0 text-micro text-ink-faint">
            {listTime(client.last_contact_at)}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted">
          <span className="tnum">{client.phone ?? 'телефон не указан'}</span>
          {client.data_complete ? (
            birth && <span className="truncate">{birth}</span>
          ) : (
            <Badge tone="warning">неполные данные</Badge>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span
            className={cn(
              'text-xs',
              client.paid_count > 0 ? 'text-ink' : 'text-ink-faint',
            )}
          >
            {purchasesLabel(client.paid_count, client.paid_amount)}
          </span>
          {client.awaiting_amount > 0 && <Badge tone="accent">ждёт оплаты</Badge>}
        </div>
      </div>
    </Link>
  )
}
