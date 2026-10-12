/**
 * Очередь ответов в интерфейсе (D-34): фильтр «Ждут дольше порога» и
 * «Взять первого в очереди» без повторов.
 *
 * Состояние лежит в sessionStorage, а не в компоненте: раздел «Чаты» пересобирается,
 * когда из пустого списка (`/chats`) открывают чат (`/chats/:id`) — это разные
 * маршруты. Без запоминания выбранный фильтр сбрасывался бы на «Все» после первого
 * же клика по строке, а список «уже взятых» терялся бы.
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

/** Добавить чат в конец списка; повторный уходит в конец, лишнее старое отбрасывается. */
export function pushTaken(taken: number[], id: number, max = MAX_TAKEN): number[] {
  return [...taken.filter((value) => value !== id), id].slice(-max)
}

/** Прочитать список из хранилища: чужой мусор превращается в пустой список, а не в ошибку. */
export function parseTaken(raw: string | null): number[] {
  if (!raw) return []
  try {
    const value: unknown = JSON.parse(raw)
    if (!Array.isArray(value)) return []
    const ids = value.filter(
      (item): item is number => typeof item === 'number' && Number.isInteger(item) && item > 0,
    )
    return [...new Set(ids)].slice(-MAX_TAKEN)
  } catch {
    return []
  }
}

/** Значение параметра `skip`; пустой список параметра не требует. */
export function skipParam(taken: number[]): string | undefined {
  return taken.length ? taken.join(',') : undefined
}

export function loadTaken(userId: number, storage: StorageLike | null = sessionStore()): number[] {
  return parseTaken(read(takenKey(userId), storage))
}

export function saveTaken(
  userId: number,
  taken: number[],
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
