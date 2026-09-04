import { Link } from 'react-router-dom'

import type { Conversation } from '@/entities/types'
import { cn } from '@/shared/lib/cn'
import { listTime, money, waitingLabel } from '@/shared/lib/format'
import { Avatar, Badge } from '@/shared/ui'

export function ChatListItem({
  conversation,
  active,
}: {
  conversation: Conversation
  active?: boolean
}) {
  const waiting = conversation.awaiting_minutes

  return (
    <Link
      to={`/chats/${conversation.id}`}
      className={cn(
        'flex items-start gap-3 border-b border-line px-4 py-3 transition-colors',
        active ? 'bg-accent-soft' : 'hover:bg-surface-raised',
      )}
    >
      <Avatar name={conversation.client.name} size="md" className="mt-0.5" />

      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-ink">
            {conversation.client.name}
          </span>
          <span className="tnum shrink-0 text-micro text-ink-faint">
            {listTime(conversation.last_message_at)}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">
            {conversation.last_message_preview ?? 'Сообщений пока нет'}
          </span>
          {conversation.unread_count > 0 && (
            <span className="tnum flex h-4 min-w-4 shrink-0 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold text-white">
              {conversation.unread_count}
            </span>
          )}
        </div>

        {/* Нижняя строка: аккаунт и состояние. Аккаунт вынесен сюда, а не к имени —
            в узкой колонке ноутбука он съедал имя клиента до пары букв. */}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-micro text-ink-faint">
            {conversation.account.title}
          </span>
          {waiting !== null && <Badge tone="danger">ждёт {waitingLabel(waiting)}</Badge>}
          {conversation.has_awaiting_deal && (
            <Badge tone="accent">
              ждёт оплаты
              {conversation.awaiting_deal_amount
                ? ` · ${money(conversation.awaiting_deal_amount)}`
                : ''}
            </Badge>
          )}
        </div>
      </div>
    </Link>
  )
}
