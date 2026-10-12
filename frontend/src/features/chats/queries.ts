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
  type QueryClient,
} from '@tanstack/react-query'

import type {
  Conversation,
  Counters,
  CursorPage,
  Message,
  Template,
  UploadResult,
} from '@/entities/types'
import { api } from '@/shared/api/client'
import { isAccountOffline } from '@/features/chats/accountLink'
import {
  pruneTaken,
  pushTaken,
  skipParam,
  takenIds,
  type TakenEntry,
} from '@/features/chats/replyQueue'

/**
 * Сервер отказал «аккаунт без связи» (409 `account_offline`) — значит, чат этого не знал:
 * событие об обрыве не дошло (связь с сервером мигнула). Не ждём его — перечитываем карточку
 * чата и список сами, иначе баннер и выключенное поле ввода не появятся.
 */
function refreshOnOffline(queryClient: QueryClient, error: unknown, conversationId: number) {
  if (!isAccountOffline(error)) return
  queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
  queryClient.invalidateQueries({ queryKey: ['conversations'] })
}

/** `awaiting_over` — ждут ответа дольше порога из настроек (то же число, что в баннере). */
export type ChatFilter = 'all' | 'awaiting' | 'awaiting_over' | 'awaiting_payment'

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
    mutationFn: (input: { text?: string; is_internal?: boolean; uploads?: UploadResult[] }) =>
      api.post<Message>(`/conversations/${conversationId}/messages`, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['counters'] })
    },
    onError: (error) => refreshOnOffline(queryClient, error, conversationId),
  })
}

/** Переслать сообщения из одного чата CRM в другой. */
export function useForwardMessages() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      targetId,
      ...body
    }: {
      targetId: number
      source_conversation_id: number
      message_ids: number[]
      hide_sender: boolean
      comment?: string
    }) =>
      api.post<{ conversation_id: number; messages: Message[]; mode: 'native' | 'copy' }>(
        `/conversations/${targetId}/forward`,
        body,
      ),
    onSuccess: (_result, variables) => {
      queryClient.invalidateQueries({ queryKey: ['messages', variables.targetId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['counters'] })
    },
    // Отказал аккаунт чата-получателя: перечитываем его, а не чат-источник.
    onError: (error, variables) => refreshOnOffline(queryClient, error, variables.targetId),
  })
}

export function useRetryMessage(conversationId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (messageId: number) =>
      api.post<Message>(`/conversations/${conversationId}/messages/${messageId}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', conversationId] })
    },
    onError: (error) => refreshOnOffline(queryClient, error, conversationId),
  })
}

export function useEditMessage(conversationId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ messageId, text }: { messageId: number; text: string }) =>
      api.patch<Message>(`/conversations/${conversationId}/messages/${messageId}`, { text }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', conversationId] })
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

/** Сколько ждущих чатов спрашиваем у сервера, чтобы найти среди взятых тех, кто написал снова. */
const WAITING_LOOKUP = 100

/**
 * «Взять первого в очереди»: сервер сам решает, какой чат открыть. `skip` — чаты, которые
 * уже брали в этой сессии: они не повторяются, пока в очереди есть другие.
 *
 * Перед запросом список «взятых» очищается от тех, кто написал снова (иначе сервер
 * пропускал бы их, пока красный баннер говорит, что клиент ждёт). Для этого нужен один
 * лёгкий запрос списка ждущих — и только если взятые есть и кто-то вообще ждёт. Не вышло
 * спросить — идём с тем списком, что помним: сама кнопка от этого не ломается.
 *
 * Возвращает чат и обновлённый список «взятых» (с этим чатом в конце) для сохранения.
 */
export async function takeNextInQueue(
  taken: TakenEntry[],
  someoneWaits: boolean,
): Promise<{ conversation: Conversation; taken: TakenEntry[] }> {
  let fresh = taken
  if (taken.length > 0 && someoneWaits) {
    try {
      const waiting = await api.get<CursorPage<Conversation>>('/conversations', {
        filter: 'awaiting',
        limit: WAITING_LOOKUP,
      })
      fresh = pruneTaken(taken, waiting.items)
    } catch {
      // Уточнить не удалось — не повод отказывать в самой кнопке
    }
  }
  const conversation = await api.get<Conversation>('/conversations/next', {
    skip: skipParam(takenIds(fresh)),
  })
  return {
    conversation,
    taken: pushTaken(fresh, { id: conversation.id, since: conversation.awaiting_reply_since }),
  }
}

export function useNextConversation() {
  return useMutation({
    mutationFn: (input: { taken: TakenEntry[]; someoneWaits: boolean }) =>
      takeNextInQueue(input.taken, input.someoneWaits),
  })
}

export function useTemplates() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: () => api.get<Template[]>('/templates'),
    staleTime: 5 * 60_000,
  })
}
