/**
 * Запросы раздела «Чаты».
 *
 * Образец для остальных разделов: ключи кэша плоские и предсказуемые,
 * страницы — курсорные, мутации точечно инвалидируют затронутое.
 */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import type {
  Conversation,
  Counters,
  CursorPage,
  Message,
  Template,
} from '@/entities/types'
import { api } from '@/shared/api/client'

export type ChatFilter = 'all' | 'awaiting' | 'awaiting_payment'

export function useCounters() {
  return useQuery({
    queryKey: ['counters'],
    queryFn: () => api.get<Counters>('/conversations/counters'),
    refetchInterval: 60_000,
  })
}

export function useConversations(params: {
  filter: ChatFilter
  q?: string
  accountId?: number | null
}) {
  return useInfiniteQuery({
    queryKey: ['conversations', params.filter, params.q ?? '', params.accountId ?? null],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<Conversation>>(
        '/conversations',
        {
          filter: params.filter,
          q: params.q || undefined,
          account_id: params.accountId ?? undefined,
          cursor: pageParam ?? undefined,
          limit: 30,
        },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

export function useConversation(id: number | null) {
  return useQuery({
    queryKey: ['conversation', id],
    queryFn: () => api.get<Conversation>(`/conversations/${id}`),
    enabled: id !== null,
  })
}

export function useMessages(conversationId: number | null) {
  return useInfiniteQuery({
    queryKey: ['messages', conversationId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<Message>>(
        `/conversations/${conversationId}/messages`,
        { cursor: pageParam ?? undefined, limit: 40 },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
    enabled: conversationId !== null,
  })
}

export function useSendMessage(conversationId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      text?: string
      is_internal?: boolean
      uploads?: {
        upload_key: string
        file_name: string
        size_bytes: number
        mime_type: string | null
      }[]
    }) =>
      api.post<Message>(`/conversations/${conversationId}/messages`, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['counters'] })
    },
  })
}

export function useMarkRead() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (conversationId: number) => api.post(`/conversations/${conversationId}/read`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['counters'] })
    },
  })
}

export function useTransfer(conversationId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (userId: number) =>
      api.post(`/conversations/${conversationId}/transfer`, { user_id: userId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
}

/** «Взять первого в очереди»: сервер сам решает, какой чат открыть. */
export function useNextConversation() {
  return useMutation({
    mutationFn: () => api.get<Conversation>('/conversations/next'),
  })
}

export function useTemplates() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: () => api.get<Template[]>('/templates'),
    staleTime: 5 * 60_000,
  })
}
