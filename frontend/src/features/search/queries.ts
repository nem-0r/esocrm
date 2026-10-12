import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { SearchResults } from '@/entities/types'
import { api } from '@/shared/api/client'

import { searchLimit, type SearchGroup } from './lib'

export type SearchType = SearchGroup

export interface HistoryItem {
  id: number
  query: string
  created_at: string
}

export const MIN_QUERY = 2

export function useSearch(query: string, types: SearchType[]) {
  const enabled = query.trim().length >= MIN_QUERY
  const limit = searchLimit(types)
  return useQuery({
    // Ключ не начинается с ['search', 'history', …]: запрос «history» не должен
    // совпасть с кэшем истории.
    queryKey: ['search', 'find', query.trim(), types.slice().sort().join(','), limit],
    queryFn: ({ signal }) =>
      api.get<SearchResults>(
        '/search',
        { q: query.trim(), types: types.length ? types.join(',') : undefined, limit },
        signal,
      ),
    enabled,
  })
}

export function useSearchHistory() {
  return useQuery({
    queryKey: ['search', 'history'],
    queryFn: ({ signal }) => api.get<HistoryItem[]>('/search/history', {}, signal),
  })
}

/**
 * История поиска пишется только по явному действию — Enter в строке или клик по
 * результату (L4). Сам поиск (набор по буквам, смена вкладки, возврат в окно)
 * историю не наполняет. Ошибка записи не должна мешать переходу по результату,
 * поэтому наружу не поднимается: история — удобство, а не часть действия.
 */
export function useRememberSearch() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (q: string) => api.post<HistoryItem[]>('/search/history', { q }),
    onSuccess: (items) => queryClient.setQueryData(['search', 'history'], items),
  })
}

export function useClearHistory() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.del<void>('/search/history'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['search', 'history'] }),
  })
}
