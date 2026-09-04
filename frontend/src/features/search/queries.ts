import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { SearchResults } from '@/entities/types'
import { api } from '@/shared/api/client'

export type SearchType = 'clients' | 'deals' | 'chats' | 'files' | 'managers'

export interface HistoryItem {
  id: number
  query: string
  created_at: string
}

export const MIN_QUERY = 2

export function useSearch(query: string, types: SearchType[]) {
  const enabled = query.trim().length >= MIN_QUERY
  return useQuery({
    queryKey: ['search', query.trim(), types.slice().sort().join(',')],
    queryFn: ({ signal }) =>
      api.get<SearchResults>(
        '/search',
        { q: query.trim(), types: types.length ? types.join(',') : undefined, limit: 5 },
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

export function useClearHistory() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.del<void>('/search/history'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['search', 'history'] }),
  })
}
