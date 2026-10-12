/**
 * Подписи и правила раздела «Оплаты».
 *
 * Статусная модель повторяет серверную (ALLOWED_DEAL_TRANSITIONS): кнопка есть
 * только тогда, когда сервер действительно примет действие. Кнопок-обманок нет.
 */

import type { DealEventKind, DealStatus } from '@/entities/types'
import { moneyIssueText, parseMoney, parseMoneyDetailed, plural } from '@/shared/lib/format'

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

/**
 * Срок действия счёта для карточки сделки: «истекает через 3 дня», «истекла» или ничего.
 *
 * Дата `expires_at` остаётся в сделке и после оплаты или отмены (она нужна выгрузке
 * и журналу), поэтому одной даты мало: у оплаченной сделки надпись «истекла» —
 * неправда. Срок имеет смысл только у счёта, который ждёт оплаты, и у истёкшего.
 */
export function expiryLabel(
  expiresAt: string | null | undefined,
  status: DealStatus,
  now: number = Date.now(),
): string | null {
  if (status === 'expired') return 'истекла'
  if (status !== 'awaiting' || !expiresAt) return null
  const left = new Date(expiresAt).getTime() - now
  if (Number.isNaN(left)) return null
  // Срок вышел, а планировщик ещё не перевёл сделку в «Истекло» — не делаем вид, что время есть.
  if (left <= 0) return 'истекла'
  if (left < 3_600_000) return 'истекает менее чем через час'
  if (left < 86_400_000) {
    const hours = Math.ceil(left / 3_600_000)
    return `истекает через ${hours} ${plural(hours, 'час', 'часа', 'часов')}`
  }
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
  /**
   * Убрана менеджером. Строка остаётся на экране приглушённой, и её можно вернуть
   * (Miro: «удалённая услуга не уходит с экрана»), но в итог, проверки и отправку
   * на сервер она не входит.
   */
  removed: boolean
}

let nextKey = 0

export function newItemDraft(): ItemDraft {
  nextKey += 1
  return { key: `item-${nextKey}`, name: '', amount: '', serviceId: null, removed: false }
}

/** Строки, которые войдут в сделку: убранные не считаются. */
export function activeItems(items: ItemDraft[]): ItemDraft[] {
  return items.filter((item) => !item.removed)
}

/** «Убрать» есть у строки, только пока активных строк больше одной: пустой состав сервер не примет. */
export function canRemoveItem(items: ItemDraft[]): boolean {
  return activeItems(items).length > 1
}

/** Пометить строку убранной. Последнюю активную не трогаем — её убрать нельзя. */
export function removeItem(items: ItemDraft[], key: string): ItemDraft[] {
  if (!canRemoveItem(items)) return items
  return items.map((item) => (item.key === key ? { ...item, removed: true } : item))
}

export function restoreItem(items: ItemDraft[], key: string): ItemDraft[] {
  return items.map((item) => (item.key === key ? { ...item, removed: false } : item))
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
  return activeItems(items).reduce((sum, item) => sum + (parseMoney(item.amount) ?? 0), 0)
}

/** Что не так с введённой суммой — текст под полем, у каждой причины свой. Пусто — всё в порядке. */
export function amountFieldError(input: string): string | null {
  const parsed = parseMoneyDetailed(input)
  return parsed.ok ? null : moneyIssueText(parsed.reason)
}

/** Ввод оборвался на разделителе: «4500,» или «4 500.» — человек печатает копейки. */
const UNFINISHED_AMOUNT = /^\d[\d\s]*[.,]$/

/**
 * Ошибка суммы для показа под полем в процессе ввода. Пока курсор в поле, завершающая
 * запятая или точка — не ошибка, а начало копеек: иначе красное сообщение (role="alert",
 * его озвучивает экранный диктор) вспыхивало бы на каждом «4500,». Когда поле покинули,
 * та же «4500,» — обычная ошибка: сервер такую сумму не примет. Отправка формы проверяет
 * сумму строго (`itemsError`), эта функция её не смягчает.
 */
export function amountLiveError(input: string, editing: boolean): string | null {
  if (editing && UNFINISHED_AMOUNT.test(input.trim())) return null
  return amountFieldError(input)
}

/** «Таро» → «таро»: текст ошибки после двоеточия начинается со строчной. */
function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1)
}

/**
 * Те же проверки, что и на сервере: пустой состав, лишние знаки и слишком большие
 * или нулевые суммы он не примет. Убранные строки не проверяются — их не отправим.
 * Причина названа точно: «больше нуля» про сумму из трёх знаков после запятой — враньё.
 */
export function itemsError(items: ItemDraft[]): string | null {
  const active = activeItems(items)
  if (active.length === 0) return 'Добавьте хотя бы одну услугу'
  if (active.some((item) => item.name.trim().length === 0)) return 'Укажите название каждой услуги'
  if (active.some((item) => item.name.trim().length > 255)) {
    return 'Название услуги — не длиннее 255 символов'
  }
  for (const item of active) {
    const problem = amountFieldError(item.amount)
    if (problem) {
      // Услуг несколько — называем, в какой ошибка; одна — и так понятно.
      return active.length > 1 ? `«${item.name.trim()}»: ${lowerFirst(problem)}` : problem
    }
  }
  return null
}

export function toItemsPayload(
  items: ItemDraft[],
): { name: string; amount: number; service_id: number | null }[] {
  return activeItems(items).map((item) => ({
    name: item.name.trim(),
    amount: parseMoney(item.amount) ?? 0,
    service_id: item.serviceId,
  }))
}
