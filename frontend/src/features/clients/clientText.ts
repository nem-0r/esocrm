/**
 * Подписи раздела «Клиенты».
 *
 * Форматирование денег и дат берётся из shared/lib/format — здесь только
 * склейка готовых кусков в фразы, которые повторяются на нескольких экранах.
 */

import type { BirthTimeApprox, ClientRow, DealStatus } from '@/entities/types'
import { birthDate, money, plural } from '@/shared/lib/format'

/** Части суток на случай, когда точное время рождения клиент не помнит. */
export const BIRTH_TIME_APPROX: Record<BirthTimeApprox, string> = {
  morning: 'утро',
  day: 'день',
  evening: 'вечер',
  night: 'ночь',
}

/** Сервер отдаёт время как «14:30:00» — секунды в интерфейсе не нужны. */
export function birthTimeLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 5)
}

type BirthTimeFields = Pick<ClientRow, 'birth_time' | 'birth_time_approx'>

/** «14:30» либо «вечер (примерно)» — приблизительное время видно как приблизительное. */
export function birthTimeFull(client: BirthTimeFields): string | null {
  if (client.birth_time) return birthTimeLabel(client.birth_time)
  if (client.birth_time_approx) return `${BIRTH_TIME_APPROX[client.birth_time_approx]} (примерно)`
  return null
}

/** «14.03.1991 · 14:30 · Москва». Пусто, когда не заполнено ничего. */
export function birthSummary(
  client: Pick<ClientRow, 'birth_date' | 'birth_city'> & BirthTimeFields,
) {
  const parts: string[] = []
  if (client.birth_date) parts.push(birthDate(client.birth_date))
  const time = birthTimeFull(client)
  if (time) parts.push(time)
  if (client.birth_city) parts.push(client.birth_city)
  return parts.join(' · ')
}

/** «5 покупок на 24 500 ₽» либо «нет покупок». */
export function purchasesLabel(count: number, amount: number): string {
  if (count === 0) return 'нет покупок'
  return `${count} ${plural(count, 'покупка', 'покупки', 'покупок')} на ${money(amount)}`
}

/** Текст ошибки для полос InlineError: ошибка всегда показывается, а не глотается. */
export function errorText(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Не удалось выполнить запрос'
}

type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger'

/** Статусы сделки одинаково называются в карточке клиента и в разделе оплат. */
export const DEAL_STATUS: Record<DealStatus, { label: string; tone: BadgeTone }> = {
  draft: { label: 'черновик', tone: 'neutral' },
  awaiting: { label: 'ждёт оплаты', tone: 'accent' },
  paid: { label: 'оплачено', tone: 'success' },
  cancelled: { label: 'отменена', tone: 'danger' },
  expired: { label: 'истекла', tone: 'warning' },
}
