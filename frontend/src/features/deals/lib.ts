/**
 * Подписи и правила раздела «Оплаты».
 *
 * Статусная модель повторяет серверную (ALLOWED_DEAL_TRANSITIONS): кнопка есть
 * только тогда, когда сервер действительно примет действие. Кнопок-обманок нет.
 */

import type { DealEventKind, DealStatus } from '@/entities/types'
import { parseMoney, plural } from '@/shared/lib/format'

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'violet'

export const DEAL_STATUS_LABEL: Record<DealStatus, string> = {
  draft: 'Черновик',
  awaiting: 'Ждёт оплаты',
  paid: 'Оплачено',
  cancelled: 'Отменено',
  expired: 'Истекло',
}

export const DEAL_STATUS_TONE: Record<DealStatus, Tone> = {
  draft: 'neutral',
  awaiting: 'accent',
  paid: 'success',
  cancelled: 'neutral',
  expired: 'warning',
}

export const DEAL_EVENT_LABEL: Record<DealEventKind, string> = {
  created: 'Сделка создана',
  sent: 'Счёт отправлен в чат',
  edited: 'Состав изменён',
  cancelled: 'Сделка отменена',
  expired: 'Срок истёк',
  paid: 'Оплата подтверждена',
  reminded: 'Отправлено напоминание',
}

export const PAYMENT_METHOD_LABEL = {
  requisites: 'Реквизиты',
  link: 'Ссылка',
} as const

/** Сообщение сервера: провайдера ещё нет, поэтому вариант «Ссылка» отключён. */
export const LINK_NOT_READY = 'Оплата по ссылке появится после подключения Робокассы'

export function canSend(status: DealStatus): boolean {
  return status === 'draft'
}

export function canPay(status: DealStatus): boolean {
  return status === 'awaiting'
}

export function canEdit(status: DealStatus): boolean {
  return status === 'draft' || status === 'awaiting'
}

export function canCancel(status: DealStatus): boolean {
  return status === 'draft' || status === 'awaiting' || status === 'expired'
}

/** «истекает через 3 дня» или «истекла». Пусто, пока счёт не отправлен. */
export function expiryLabel(expiresAt: string | null | undefined): string | null {
  if (!expiresAt) return null
  const left = new Date(expiresAt).getTime() - Date.now()
  if (Number.isNaN(left)) return null
  if (left <= 0) return 'истекла'
  const days = Math.ceil(left / 86_400_000)
  return `истекает через ${days} ${plural(days, 'день', 'дня', 'дней')}`
}

export function daysWithoutAnswerLabel(days: number): string {
  return `${days} ${plural(days, 'день', 'дня', 'дней')} без ответа`
}

/* ------------------------------------------------- черновик состава услуг */

export interface ItemDraft {
  key: string
  name: string
  /** Рубли как их набрал менеджер. В копейки переводит parseMoney. */
  amount: string
  /** Услуга из справочника. Пусто — название вписано вручную. */
  serviceId: number | null
}

let nextKey = 0

export function newItemDraft(): ItemDraft {
  nextKey += 1
  return { key: `item-${nextKey}`, name: '', amount: '', serviceId: null }
}

/**
 * Сравнение названий услуг: без регистра, без пробелов по краям и без
 * различия «е»/«ё» — человек читает «расчет» и «Расчёт» как одно и то же,
 * а обычный `toLowerCase` эту букву не выравнивает. Используется и в поиске
 * по справочнику (аудит №9), и при автосвязывании вручную введённого
 * названия с услугой (аудит №10 — чтобы для новых позиций дубли в
 * статистике не появлялись вовсе).
 */
export function normalizeServiceName(value: string): string {
  return value.trim().toLowerCase().replace(/ё/g, 'е')
}

/**
 * Есть ли в черновике оплаты что-то введённое менеджером. Используется, чтобы
 * не закрывать окно оплаты молча при случайном Esc или клике мимо — введённое
 * жалко терять (аудит №4).
 */
export function hasDealDraft(items: ItemDraft[], extraFields: string[] = []): boolean {
  return (
    items.some((item) => item.name.trim().length > 0 || item.amount.trim().length > 0) ||
    extraFields.some((value) => value.trim().length > 0)
  )
}

/** Копейки → текст поля: «4500» или «4500,50» — копейки не теряем. */
export function amountInput(kopecks: number): string {
  const whole = Math.trunc(kopecks / 100)
  const rest = Math.abs(kopecks % 100)
  return rest ? `${whole},${String(rest).padStart(2, '0')}` : String(whole)
}

export function draftTotal(items: ItemDraft[]): number {
  return items.reduce((sum, item) => sum + (parseMoney(item.amount) ?? 0), 0)
}

/** Те же проверки, что и на сервере: пустой состав и нулевые суммы он не примет. */
export function itemsError(items: ItemDraft[]): string | null {
  if (items.length === 0) return 'Добавьте хотя бы одну услугу'
  if (items.some((item) => item.name.trim().length === 0)) return 'Укажите название каждой услуги'
  if (items.some((item) => item.name.trim().length > 255)) {
    return 'Название услуги — не длиннее 255 символов'
  }
  if (items.some((item) => parseMoney(item.amount) === null)) {
    return 'Стоимость каждой услуги должна быть больше нуля'
  }
  return null
}

export function toItemsPayload(
  items: ItemDraft[],
): { name: string; amount: number; service_id: number | null }[] {
  return items.map((item) => ({
    name: item.name.trim(),
    amount: parseMoney(item.amount) ?? 0,
    service_id: item.serviceId,
  }))
}
