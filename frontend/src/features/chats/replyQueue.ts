/**
 * Очередь ответов в интерфейсе (D-34): фильтр «Ждут дольше порога», «Взять первого в
 * очереди» без повторов и текст поиска по чатам.
 *
 * Состояние лежит вне компонента (sessionStorage, адрес), а не в нём: раздел «Чаты»
 * пересобирается, когда из пустого списка (`/chats`) открывают чат (`/chats/:id`) — это
 * разные маршруты. Без запоминания выбранный фильтр сбрасывался бы на «Все» после первого
 * же клика по строке, а список «уже взятых» терялся бы. Текст поиска запоминается в адресе
 * (`?q=`) — см. ниже.
 *
 * sessionStorage бывает недоступен (приватное окно, запрет данных сайта) — тогда
 * работаем из памяти страницы: отказать может только запоминание, не сами кнопки.
 */

import type { ChatFilter } from '@/features/chats/queries'

/** Столько номеров принимает сервер в `GET /conversations/next?skip=…`. */
export const MAX_TAKEN = 50

/** Код 404 от сервера: все ждущие чаты в этой сессии уже взяты. */
export const QUEUE_DONE = 'queue_done'

export type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const FILTER_KEY = 'crm.chats.filter'
const FILTERS: readonly ChatFilter[] = ['all', 'awaiting', 'awaiting_over', 'awaiting_payment']

const memory = new Map<string, string>()

function sessionStore(): StorageLike | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function read(key: string, storage: StorageLike | null): string | null {
  if (storage) {
    try {
      return storage.getItem(key)
    } catch {
      // Хранилище отключено — берём то, что помним сами
    }
  }
  return memory.get(key) ?? null
}

function write(key: string, value: string | null, storage: StorageLike | null): void {
  if (value === null) memory.delete(key)
  else memory.set(key, value)
  if (!storage) return
  try {
    if (value === null) storage.removeItem(key)
    else storage.setItem(key, value)
  } catch {
    // Не записалось — останется в памяти страницы
  }
}

// --- взятые в этой сессии чаты ------------------------------------------------

const takenKey = (userId: number) => `crm.chats.taken.${userId}`

/**
 * Взятый чат. `since` — с какого момента он ждал ответа, когда его взяли (`awaiting_reply_since`
 * с сервера, не часы браузера: у часов может быть сдвиг). `null` — чат не ждал: его отдали
 * как «самый свежий», когда ждущих не было.
 */
export interface TakenEntry {
  id: number
  since: string | null
}

/** Кто сейчас ждёт ответа: нужно, чтобы понять, что взятый чат ждёт заново. */
export interface WaitingRow {
  id: number
  awaiting_reply_since: string | null
}

/** Добавить чат в конец списка; повторный уходит в конец, лишнее старое отбрасывается. */
export function pushTaken(taken: TakenEntry[], entry: TakenEntry, max = MAX_TAKEN): TakenEntry[] {
  return [...taken.filter((value) => value.id !== entry.id), entry].slice(-max)
}

/** Номера для параметра `skip`. */
export function takenIds(taken: TakenEntry[]): number[] {
  return taken.map((entry) => entry.id)
}

function isChatId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

/**
 * Прочитать список из хранилища: чужой мусор превращается в пустой список, а не в ошибку.
 * Раньше хранились голые номера — их тоже понимаем (как «не ждал»), чтобы вкладка, открытая
 * до обновления, не потеряла список.
 */
export function parseTaken(raw: string | null): TakenEntry[] {
  if (!raw) return []
  try {
    const value: unknown = JSON.parse(raw)
    if (!Array.isArray(value)) return []
    const entries: TakenEntry[] = []
    for (const item of value) {
      if (isChatId(item)) {
        entries.push({ id: item, since: null })
      } else if (item && typeof item === 'object' && isChatId((item as TakenEntry).id)) {
        const since = (item as TakenEntry).since
        entries.push({ id: (item as TakenEntry).id, since: typeof since === 'string' ? since : null })
      }
    }
    // Повтор номера — последняя запись побеждает, порядок «давно взятые первыми» сохраняется.
    const seen = new Set<number>()
    const unique: TakenEntry[] = []
    for (const entry of entries.reverse()) {
      if (seen.has(entry.id)) continue
      seen.add(entry.id)
      unique.push(entry)
    }
    return unique.reverse().slice(-MAX_TAKEN)
  } catch {
    return []
  }
}

/** Значение параметра `skip`; пустой список параметра не требует. */
export function skipParam(taken: number[]): string | undefined {
  return taken.length ? taken.join(',') : undefined
}

/**
 * Убрать из «взятых» чаты, которые ждут ответа ЗАНОВО: клиент написал после того, как чат
 * взяли и ответили. Сервер пропускает всё из `skip`, и без этого такой клиент остался бы
 * невидимым для «Взять первого» при красном баннере, а когда он последний в очереди — кнопка
 * говорила бы «чатов больше нет».
 *
 * Чат, который сейчас не ждёт, остаётся в списке: ждущих он не касается, а когда не ждёт никто,
 * «Взять первого» ходит по недавним чатам и не должен возвращаться к только что обработанному.
 * Чат с тем же началом ожидания (его взяли и ещё не ответили) тоже остаётся — это и есть
 * «уже открывал». Не удалось сравнить время — не трогаем: хуже оставить, чем потерять.
 */
export function pruneTaken(taken: TakenEntry[], waiting: WaitingRow[]): TakenEntry[] {
  const sinceById = new Map<number, string>()
  for (const row of waiting) {
    if (row.awaiting_reply_since) sinceById.set(row.id, row.awaiting_reply_since)
  }
  return taken.filter((entry) => {
    const now = sinceById.get(entry.id)
    if (now === undefined) return true // не ждёт
    if (entry.since === null) return false // не ждал, когда брали, — а теперь ждёт
    const was = Date.parse(entry.since)
    const current = Date.parse(now)
    return !(Number.isFinite(was) && Number.isFinite(current) && current > was)
  })
}

/**
 * Что сказать, когда сервер ответил `queue_done`. Ждущие есть — значит, все они уже открыты
 * в этой сессии: так и пишем, а не «чатов больше нет» (на красном баннере это выглядело бы
 * ошибкой). Ждущих нет — перебраны все недавние чаты.
 */
export function queueDoneText(waitingCount: number): string {
  return waitingCount > 0
    ? 'Все ждущие чаты вы уже открывали. Нажмите ещё раз — начнём сначала'
    : 'Больше нет чатов в очереди. Нажмите ещё раз — начнём сначала'
}

export function loadTaken(userId: number, storage: StorageLike | null = sessionStore()): TakenEntry[] {
  return parseTaken(read(takenKey(userId), storage))
}

export function saveTaken(
  userId: number,
  taken: TakenEntry[],
  storage: StorageLike | null = sessionStore(),
): void {
  write(takenKey(userId), JSON.stringify(taken), storage)
}

export function clearTaken(userId: number, storage: StorageLike | null = sessionStore()): void {
  write(takenKey(userId), null, storage)
}

// --- выбранный фильтр списка --------------------------------------------------

export function isChatFilter(value: unknown): value is ChatFilter {
  return typeof value === 'string' && (FILTERS as readonly string[]).includes(value)
}

export function loadFilter(storage: StorageLike | null = sessionStore()): ChatFilter {
  const value = read(FILTER_KEY, storage)
  return isChatFilter(value) ? value : 'all'
}

export function saveFilter(filter: ChatFilter, storage: StorageLike | null = sessionStore()): void {
  write(FILTER_KEY, filter, storage)
}

/** Подпись фильтра «ждут дольше порога»: число минут — из настроек, не зашитое. */
export function overFilterLabel(minutes: number | null | undefined): string {
  return minutes ? `Ждут >${minutes} мин` : 'Ждут дольше порога'
}

// --- текст поиска по чатам ----------------------------------------------------

/**
 * Текст поиска живёт в адресе (`/chats?q=…`, `/chats/12?q=…`). Раздел пересобирается, когда из
 * пустого списка открывают первый чат, — состояние компонента при этом терялось, а адрес
 * переживает и пересборку, и «Назад» на телефоне, и обновление страницы.
 */
export const SEARCH_PARAM = 'q'

/** Столько знаков принимает сервер в `GET /conversations?q=…`; длиннее — 422 вместо списка. */
export const MAX_SEARCH = 200

export function searchFromParams(params: URLSearchParams): string {
  return (params.get(SEARCH_PARAM) ?? '').trim().slice(0, MAX_SEARCH)
}

/** Те же параметры с новым текстом поиска; пустой текст убирает параметр, остальное не трогает. */
export function withSearch(params: URLSearchParams, text: string): URLSearchParams {
  const next = new URLSearchParams(params)
  const value = text.trim().slice(0, MAX_SEARCH)
  if (value) next.set(SEARCH_PARAM, value)
  else next.delete(SEARCH_PARAM)
  return next
}

/** Адрес чата с сохранённым поиском: открыли чат — список слева остался таким же. */
export function chatPath(conversationId: number, search: string): string {
  const query = withSearch(new URLSearchParams(), search).toString()
  return query ? `/chats/${conversationId}?${query}` : `/chats/${conversationId}`
}
