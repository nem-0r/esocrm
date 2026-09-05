/**
 * Словари и мелкие помощники раздела «Профиль».
 *
 * Здесь нет ни одного значения, придуманного «на всякий случай»: подписи взяты
 * с доски, а список часовых поясов — те, в которых реально работает команда.
 */

import type { AccountStatus, FunnelStage, UserRole } from '@/entities/types'
import { ApiError } from '@/shared/api/client'
import { dateTimeFull } from '@/shared/lib/format'

type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'violet'

// Названия этапов заданы заказчиком (ТЗ от 01.09.2026, п. 1.5). Ключи в базе
// остались прежними — переименование чисто в подписях, миграция не нужна.
export const FUNNEL_LABEL: Record<FunnelStage, string> = {
  warmup: 'Бот',
  diagnostic: 'Первые продажи',
  sales: 'Допы',
}

export const FUNNEL_OPTIONS: { value: FunnelStage; label: string }[] = [
  { value: 'warmup', label: FUNNEL_LABEL.warmup },
  { value: 'diagnostic', label: FUNNEL_LABEL.diagnostic },
  { value: 'sales', label: FUNNEL_LABEL.sales },
]

export const ROLE_LABEL: Record<UserRole, string> = {
  admin: 'Руководитель',
  manager: 'Менеджер',
}

export const ROLE_OPTIONS: { value: UserRole; label: string; hint: string }[] = [
  { value: 'manager', label: ROLE_LABEL.manager, hint: 'Видит только свои аккаунты' },
  { value: 'admin', label: ROLE_LABEL.admin, hint: 'Видит всё, управляет аккаунтами и людьми' },
]

export const STATUS_LABEL: Record<AccountStatus, string> = {
  pending: 'ожидает подключения',
  connected: 'подключён',
  error: 'нет связи',
  disconnected: 'отключён',
}

export const STATUS_TONE: Record<AccountStatus, Tone> = {
  pending: 'warning',
  connected: 'success',
  error: 'danger',
  disconnected: 'neutral',
}

/** Дни недели в формате ISO: 1 — понедельник, 7 — воскресенье. */
export const WEEK_DAYS: { value: number; short: string }[] = [
  { value: 1, short: 'Пн' },
  { value: 2, short: 'Вт' },
  { value: 3, short: 'Ср' },
  { value: 4, short: 'Чт' },
  { value: 5, short: 'Пт' },
  { value: 6, short: 'Сб' },
  { value: 7, short: 'Вс' },
]

/** Черновик графика в форме: дни и часы всегда заполнены, даже когда график выключен —
 *  так галочка «включить» не заставляет заново набирать часы. */
export interface WorkScheduleDraft {
  enabled: boolean
  days: number[]
  start: string
  end: string
}

export const EMPTY_SCHEDULE: WorkScheduleDraft = {
  enabled: false,
  days: [1, 2, 3, 4, 5],
  start: '10:00',
  end: '19:00',
}

export const TIMEZONES: { value: string; label: string }[] = [
  { value: 'Europe/Kaliningrad', label: 'Калининград, UTC+2' },
  { value: 'Europe/Moscow', label: 'Москва, UTC+3' },
  { value: 'Europe/Samara', label: 'Самара, UTC+4' },
  { value: 'Asia/Yekaterinburg', label: 'Екатеринбург, UTC+5' },
  { value: 'Asia/Almaty', label: 'Алматы, UTC+5' },
  { value: 'Asia/Omsk', label: 'Омск, UTC+6' },
  { value: 'Asia/Krasnoyarsk', label: 'Красноярск, UTC+7' },
  { value: 'Asia/Irkutsk', label: 'Иркутск, UTC+8' },
  { value: 'Asia/Yakutsk', label: 'Якутск, UTC+9' },
  { value: 'Asia/Vladivostok', label: 'Владивосток, UTC+10' },
  { value: 'Asia/Magadan', label: 'Магадан, UTC+11' },
  { value: 'Asia/Kamchatka', label: 'Камчатка, UTC+12' },
]

/** Система налогообложения для чека 54-ФЗ — значения из формата Робокассы. */
export const ROBOKASSA_SNO_OPTIONS: { value: string; label: string }[] = [
  { value: 'osn', label: 'ОСН — общая' },
  { value: 'usn_income', label: 'УСН — доходы' },
  { value: 'usn_income_outcome', label: 'УСН — доходы минус расходы' },
  { value: 'esn', label: 'ЕСН' },
  { value: 'patent', label: 'Патент' },
]

export const ROBOKASSA_TAX_OPTIONS: { value: string; label: string }[] = [
  { value: 'none', label: 'Без НДС' },
  { value: 'vat0', label: 'НДС 0%' },
  { value: 'vat10', label: 'НДС 10%' },
  { value: 'vat20', label: 'НДС 20%' },
  { value: 'vat110', label: 'НДС 10/110' },
  { value: 'vat120', label: 'НДС 20/120' },
]

/** «Более 5 минут отсутствия — офлайн» (бизнес-правила, §7). */
const ONLINE_WINDOW_MS = 5 * 60_000

export function isOnline(lastSeenAt: string | null | undefined): boolean {
  if (!lastSeenAt) return false
  return Date.now() - new Date(lastSeenAt).getTime() < ONLINE_WINDOW_MS
}

export function presenceLabel(online: boolean, lastSeenAt: string | null | undefined): string {
  if (online) return 'в сети'
  if (!lastSeenAt) return 'ещё не заходил'
  return `был в сети ${dateTimeFull(lastSeenAt)}`
}

export function errorMessage(error: unknown, fallback = 'Не удалось выполнить запрос'): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return fallback
}

/**
 * Ошибки по полям из ответа сервера: `details.fields` либо сам `details`.
 * Показываются под тем полем, к которому относятся, а не одной строкой сверху.
 */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const raw: unknown = error.details.fields ?? error.details
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {}
  const result: Record<string, string> = {}
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === 'string') result[key] = value
  }
  return result
}

/** Сервер отдаёт относительный путь `/invite/{token}` — сотруднику нужен полный адрес. */
export function inviteLink(path: string): string {
  if (/^https?:\/\//.test(path)) return path
  return `${window.location.origin}${path.startsWith('/') ? path : `/${path}`}`
}
