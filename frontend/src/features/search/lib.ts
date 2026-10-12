import type { SearchResults } from '@/entities/types'

/** Группа результатов поиска совпадает с названием вкладки-фильтра. */
export type SearchGroup = 'clients' | 'deals' | 'chats' | 'files' | 'managers'

/** Сколько строк каждой группы показываем в общем предпросмотре. */
export const PREVIEW_LIMIT = 5
/** Предел «Показать все»: столько же отдаёт сервер, больше он не читает. */
export const FULL_LIMIT = 50

/**
 * Сколько строк просить у сервера. Выбрана одна вкладка («Клиенты») — это уже
 * «показать все», до предела; иначе — короткий предпросмотр по каждой группе.
 */
export function searchLimit(types: SearchGroup[]): number {
  return types.length === 1 ? FULL_LIMIT : PREVIEW_LIMIT
}

/**
 * Что писать под именем клиента: @username и телефон, что есть. Служебный id —
 * только когда больше показать нечего (иначе «id 703 · телефон» ничего не говорит).
 */
export function clientHint(row: {
  id: number
  tg_username?: string | null
  phone?: string | null
}): string {
  const parts = [row.tg_username ? `@${row.tg_username}` : null, row.phone || null].filter(
    (part): part is string => Boolean(part),
  )
  return parts.length > 0 ? parts.join(' · ') : `id ${row.id}`
}

/** Сколько всего найдено в группе. Старый сервер `totals` не присылает — тогда сколько пришло. */
export function groupTotal(results: SearchResults, group: SearchGroup): number {
  const shown = (results[group] ?? []).length
  const total = results.totals?.[group]
  return typeof total === 'number' ? Math.max(total, shown) : shown
}

/** Число для подписи. Сервер читает на одну строку больше предела, чтобы честно сказать «больше». */
export function totalLabel(total: number): string {
  return total > FULL_LIMIT ? `${FULL_LIMIT}+` : String(total)
}

export function showAllLabel(total: number): string {
  return `Показать все (${totalLabel(total)})`
}

/** Есть что докрутить: найдено больше, чем показано, и мы ещё не в режиме одной вкладки. */
export function canShowAll(
  results: SearchResults,
  group: SearchGroup,
  types: SearchGroup[],
): boolean {
  if (searchLimit(types) === FULL_LIMIT) return false
  return groupTotal(results, group) > (results[group] ?? []).length
}

/** Даже полный список обрезан пределом: человеку надо сказать, что запрос стоит уточнить. */
export function isTruncated(
  results: SearchResults,
  group: SearchGroup,
  types: SearchGroup[],
): boolean {
  if (searchLimit(types) !== FULL_LIMIT) return false
  return groupTotal(results, group) > (results[group] ?? []).length
}
