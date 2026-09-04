/**
 * Форматирование единообразно для всего интерфейса.
 * Деньги приходят с сервера целыми копейками — рубли получаются только здесь.
 */

const MONTHS_SHORT = [
  'янв',
  'фев',
  'мар',
  'апр',
  'мая',
  'июн',
  'июл',
  'авг',
  'сен',
  'окт',
  'ноя',
  'дек',
]

const MONTHS_FULL = [
  'января',
  'февраля',
  'марта',
  'апреля',
  'мая',
  'июня',
  'июля',
  'августа',
  'сентября',
  'октября',
  'ноября',
  'декабря',
]

/** 490000 копеек → «4 900 ₽». Копейки показываем только если они есть. */
export function money(kopecks: number | null | undefined): string {
  if (kopecks === null || kopecks === undefined) return '—'
  const sign = kopecks < 0 ? '−' : ''
  const abs = Math.abs(kopecks)
  const whole = Math.floor(abs / 100)
  const cents = abs % 100
  // Intl сам ставит неразрывный пробел в разрядах, поэтому «4 900 ₽»
  // не переносится по строкам. Своя замена пробела тут не нужна.
  const grouped = whole.toLocaleString('ru-RU')
  return cents === 0
    ? `${sign}${grouped} ₽`
    : `${sign}${grouped},${String(cents).padStart(2, '0')} ₽`
}

/** Без знака валюты — для полей ввода. */
export function moneyPlain(kopecks: number): string {
  return String(Math.round(kopecks / 100))
}

export function parseMoney(input: string): number | null {
  const cleaned = input.replace(/[^\d,.]/g, '').replace(',', '.')
  if (!cleaned) return null
  const value = Number(cleaned)
  if (!Number.isFinite(value) || value <= 0) return null
  return Math.round(value * 100)
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

export function time(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function dateShort(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`
}

export function dateFull(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getDate()} ${MONTHS_FULL[d.getMonth()]} ${d.getFullYear()}`
}

export function dateTimeFull(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getDate()} ${MONTHS_FULL[d.getMonth()]} ${d.getFullYear()}, ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Дата рождения: «14.03.1991». */
export function birthDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-')
  return `${d}.${m}.${y}`
}

/** Время последнего сообщения в списке чатов: сегодня — часы, вчера — «вчера», дальше — дата. */
export function listTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return time(iso)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (d.toDateString() === yesterday.toDateString()) return 'вчера'
  return dateShort(iso)
}

/** Время ожидания ответа: «42 мин», «3 ч 10 мин», «2 дня». Единица подписана всегда. */
export function waitingLabel(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return ''
  if (minutes < 60) return `${minutes} мин`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) {
    const rest = minutes % 60
    return rest ? `${hours} ч ${rest} мин` : `${hours} ч`
  }
  const days = Math.floor(hours / 24)
  return `${days} ${plural(days, 'день', 'дня', 'дней')}`
}

export function durationLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const minutes = Math.round(seconds / 60)
  // «0 мин» выглядит как отсутствие данных, хотя ответ был — просто быстрый.
  if (minutes < 1) return '<1 мин'
  return waitingLabel(minutes)
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return isoDate(d)
}

/** Заголовок разделителя в ленте: «сегодня», «вчера» или дата. */
export function dayDivider(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) return 'сегодня'
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (d.toDateString() === yesterday.toDateString()) return 'вчера'
  const sameYear = d.getFullYear() === now.getFullYear()
  return sameYear ? dateFull(iso).replace(` ${d.getFullYear()}`, '') : dateFull(iso)
}

/** Ключ дня — по нему решаем, нужен ли разделитель. */
export function dayKey(iso: string): string {
  return new Date(iso).toDateString()
}
