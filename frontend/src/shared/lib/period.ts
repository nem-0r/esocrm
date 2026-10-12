/**
 * Периоды «Неделя / Месяц / Квартал / Год / Свой» — одни на «Оплаты» и «Статистику».
 *
 * Раньше у каждого раздела были свои пресеты, и «Месяц» значил разное: в оплатах —
 * скользящие 30 дней, в статистике — с 1-го числа, поэтому одна и та же сумма
 * «за месяц» расходилась (D-39). Теперь «Месяц» — календарный везде.
 *
 * Все даты — строки «ГГГГ-ММ-ДД». Функции чистые: «сегодня» приходит параметром,
 * поэтому их можно проверить без часов.
 */

import { dateFull } from '@/shared/lib/format'

export type PeriodPreset = 'week' | 'month' | 'quarter' | 'year' | 'custom'

export interface DateRange {
  from: string
  to: string
}

/** Границы здравого смысла — те же, что в запросах сервера (stats.py, deals.py). */
export const MIN_PERIOD_DATE = '1900-01-01'
export const MAX_PERIOD_DATE = '2100-12-31'

/** Сколько дней назад начинается пресет (считая от сегодня). Месяц — отдельно, он календарный. */
const PRESET_DAYS = { week: 6, quarter: 89, year: 364 } as const

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** Дата из строки «ГГГГ-ММ-ДД», либо null, если такого дня в календаре нет (30 февраля). */
function parseIso(iso: string): Date | null {
  const match = ISO_DATE.exec(iso)
  if (!match) return null
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  // Не Date.UTC: тот читает годы 0–99 как 1900–1999, а «0002» — это то, что человек
  // печатает в поле даты по дороге к «2026».
  const date = new Date(0)
  date.setUTCFullYear(year, month - 1, day)
  // setUTCFullYear сам «переносит» 30 февраля на март — сверяем, что день остался тем же.
  const same =
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  return same ? date : null
}

function formatIso(date: Date): string {
  return `${String(date.getUTCFullYear()).padStart(4, '0')}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`
}

/** Сдвиг даты на целое число дней. Считается в UTC, поэтому переход на летнее время не мешает. */
export function addDays(iso: string, days: number): string {
  const date = parseIso(iso)
  if (!date) return iso
  date.setUTCDate(date.getUTCDate() + days)
  return formatIso(date)
}

/** Пресет периода по состоянию на `today`. «Свой» сюда не приходит — он хранится как введён. */
export function presetRange(preset: Exclude<PeriodPreset, 'custom'>, today: string): DateRange {
  if (preset === 'month') return { from: `${today.slice(0, 7)}-01`, to: today }
  return { from: addDays(today, -PRESET_DAYS[preset]), to: today }
}

/**
 * Сегодняшняя дата в часовом поясе организации (настройка `timezone`). Сервер
 * считает «сегодня» именно по нему, так что вечером по Москве интерфейс и сервер
 * не расходятся на день. Без настройки или с непонятным поясом — по часам браузера.
 */
export function todayInZone(timeZone?: string | null, now: Date = new Date()): string {
  if (timeZone) {
    try {
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).formatToParts(now)
      const part = (type: string) => parts.find((p) => p.type === type)?.value
      const [y, m, d] = [part('year'), part('month'), part('day')]
      if (y && m && d) return `${y}-${m}-${d}`
    } catch {
      // Неизвестный пояс — падаем на часы браузера ниже.
    }
  }
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * Что не так с введённым периодом, или null, если всё в порядке. Один и тот же текст
 * показывается под полями дат и вместо списка; пока он есть — запрос на сервер не уходит.
 * Пустая дата — тоже ошибка: раньше она молча отбрасывалась, и применялся период по
 * умолчанию, который человек не выбирал.
 */
export function rangeError(from: string, to: string): string | null {
  if (!from || !to) return 'Укажите обе даты периода'
  if (!parseIso(from) || !parseIso(to)) return 'Дата указана неверно'
  if (from < MIN_PERIOD_DATE || from > MAX_PERIOD_DATE || to < MIN_PERIOD_DATE || to > MAX_PERIOD_DATE) {
    return 'Период должен лежать между 1900 и 2100 годами'
  }
  if (from > to) return 'Начало периода позже конца'
  return null
}

/** «1 октября — 7 октября 2026». Год один раз в конце, если период в пределах одного года. */
export function rangeLabel(range: DateRange): string {
  const to = dateFull(`${range.to}T00:00:00`)
  if (range.from === range.to) return to
  const from = dateFull(`${range.from}T00:00:00`)
  const sameYear = range.from.slice(0, 4) === range.to.slice(0, 4)
  return `${sameYear ? from.replace(/ \d{4}$/, '') : from} — ${to}`
}
