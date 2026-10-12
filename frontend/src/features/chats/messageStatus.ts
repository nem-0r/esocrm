/**
 * Значок статуса сообщения — общий для пузыря в чате и превью в списке чатов (D-36).
 *
 * Статуса «доставлено» нет намеренно: MTProto его не отдаёт. Как в Telegram:
 * часы — отправляется, одна галочка — отправлено, две — прочитано, красный «!» —
 * не доставлено. Функция чистая: какая иконка и какая подпись — решается здесь,
 * рисует их `MessageStatusIcon`.
 */

import type { Direction, MessageStatus } from '@/entities/types'

export type DeliveryState = 'sending' | 'sent' | 'read' | 'failed'

export interface DeliveryView {
  state: DeliveryState
  /** Подпись для `title` и `aria-label`. */
  label: string
}

const VIEW: Record<MessageStatus, DeliveryView> = {
  queued: { state: 'sending', label: 'Отправляется' },
  sent: { state: 'sent', label: 'Отправлено' },
  read: { state: 'read', label: 'Прочитано' },
  failed: { state: 'failed', label: 'Не доставлено' },
}

/**
 * Что нарисовать у сообщения. Значок только у исходящего: у входящего статус в
 * базе есть, но показывать его некому. `direction` по умолчанию исходящее — так
 * зовёт пузырь, где направление уже известно; список чатов передаёт его явно.
 * Нет статуса (пустой чат, служебная заметка, старый сервер) или он незнаком —
 * значка нет, неверный лучше не рисовать.
 */
export function deliveryView(
  status: MessageStatus | null | undefined,
  direction: Direction | null | undefined = 'out',
): DeliveryView | null {
  if (direction !== 'out' || !status) return null
  return VIEW[status] ?? null
}
