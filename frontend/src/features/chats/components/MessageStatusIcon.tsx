import { AlertCircle, Check, CheckCheck, Clock } from 'lucide-react'

import type { Direction, MessageStatus } from '@/entities/types'
import { cn } from '@/shared/lib/cn'

import { deliveryView, type DeliveryState } from '@/features/chats/messageStatus'

const ICON = {
  sending: Clock,
  sent: Check,
  read: CheckCheck,
  failed: AlertCircle,
} satisfies Record<DeliveryState, typeof Clock>

const TONE: Record<DeliveryState, string> = {
  sending: 'text-ink-faint',
  sent: 'text-ink-faint',
  read: 'text-accent-text',
  failed: 'text-danger',
}

/**
 * Статус исходящего сообщения: часы, галочка, две галочки или красный «!».
 * Один и тот же значок в пузыре чата и в превью списка (D-36); что и когда
 * показывать, решает `deliveryView`. Без статуса или у входящего — ничего.
 */
export function MessageStatusIcon({
  status,
  direction,
  className,
}: {
  status: MessageStatus | null | undefined
  /** Не передан — исходящее (так зовёт пузырь); список чатов передаёт направление явно. */
  direction?: Direction | null
  className?: string
}) {
  const view = deliveryView(status, direction)
  if (!view) return null
  const Icon = ICON[view.state]
  return (
    <span
      role="img"
      aria-label={view.label}
      title={view.label}
      className="inline-flex shrink-0 items-center"
    >
      <Icon className={cn('size-3.5', TONE[view.state], className)} aria-hidden />
    </span>
  )
}
