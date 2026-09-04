/**
 * Запросы раздела «Клиенты».
 *
 * Ключи кэша плоские, страницы курсорные, мутации точечно инвалидируют
 * затронутое — как в разделе «Чаты», он здесь образец.
 */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import type {
  AttachmentRef,
  BirthTimeApprox,
  ClientCard,
  ClientRow,
  CursorPage,
  DealRow,
  Direction,
  UserRef,
} from '@/entities/types'
import { api, downloadFile } from '@/shared/api/client'
import { daysAgo, isoDate } from '@/shared/lib/format'

export type ClientSort = 'last_contact' | 'amount'

/**
 * Заметка менеджера. В entities/types её нет: она встречается только здесь,
 * поэтому форма ответа сервера описана рядом с запросом.
 */
export interface ClientNote {
  id: number
  text: string
  author: UserRef
  created_at: string
}

/** Материал — вложение из переписки плюс кто и когда его отправил. */
export interface ClientMaterial extends AttachmentRef {
  created_at: string
  direction: Direction
  author: UserRef | null
}

/** Тело PATCH. Отсутствующее поле сервер не трогает, null — очищает. */
export interface ClientPatch {
  display_name?: string | null
  phone?: string | null
  birth_date?: string | null
  birth_time?: string | null
  birth_time_approx?: BirthTimeApprox | null
  marketing_consent?: boolean
  birth_city?: string | null
  source?: string | null
}

const PAGE = 30

/** Период выгрузки в MVP зафиксирован: последний год. */
export const EXPORT_PERIOD_DAYS = 365

export function useClients(params: { q?: string; sort: ClientSort }) {
  return useInfiniteQuery({
    queryKey: ['clients', params.sort, params.q ?? ''],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<ClientRow>>(
        '/clients',
        {
          q: params.q || undefined,
          sort: params.sort,
          cursor: pageParam ?? undefined,
          limit: PAGE,
        },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

export function useClient(id: number | null) {
  return useQuery({
    queryKey: ['client', id],
    queryFn: () => api.get<ClientCard>(`/clients/${id}`),
    enabled: id !== null,
  })
}

export function useUpdateClient(id: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (patch: ClientPatch) => api.patch<ClientCard>(`/clients/${id}`, patch),
    onSuccess: (card) => {
      queryClient.setQueryData(['client', id], card)
      queryClient.invalidateQueries({ queryKey: ['client', id] })
      queryClient.invalidateQueries({ queryKey: ['clients'] })
    },
  })
}

export function useClientNotes(clientId: number) {
  return useInfiniteQuery({
    queryKey: ['client-notes', clientId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<ClientNote>>(
        `/clients/${clientId}/notes`,
        { cursor: pageParam ?? undefined, limit: PAGE },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

export function useCreateNote(clientId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (text: string) => api.post<ClientNote>(`/clients/${clientId}/notes`, { text }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['client-notes', clientId] })
    },
  })
}

/** Чужую заметку сервер не отдаёт удалить: отвечает 404. Ошибку показываем как есть. */
export function useDeleteNote(clientId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (noteId: number) => api.del(`/clients/${clientId}/notes/${noteId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['client-notes', clientId] })
    },
  })
}

export function useClientMaterials(clientId: number) {
  return useInfiniteQuery({
    queryKey: ['client-materials', clientId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<ClientMaterial>>(
        `/clients/${clientId}/materials`,
        { cursor: pageParam ?? undefined, limit: PAGE },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

/**
 * Оплаты клиента.
 *
 * Отдельного `/clients/{id}/deals` на сервере нет — список оплат фильтруется
 * по клиенту. Период у `/deals` по умолчанию последний год, а в карточке нужны
 * все сделки, поэтому началом периода берём дату первого обращения: раньше неё
 * сделок у клиента быть не может.
 */
export function useClientDeals(clientId: number, sinceIso: string) {
  const dateFrom = sinceIso.slice(0, 10)
  return useInfiniteQuery({
    queryKey: ['client-deals', clientId, dateFrom],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<DealRow>>(
        '/deals',
        {
          client_id: clientId,
          date_from: dateFrom,
          cursor: pageParam ?? undefined,
          limit: PAGE,
        },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

/** Выгрузка CSV за последний год. Файл отдаёт сервер, права проверяет он же. */
export function useExportClients() {
  return useMutation({
    mutationFn: () => {
      const today = isoDate(new Date())
      return downloadFile('/clients/export', `clients-${today}.csv`, {
        date_from: daysAgo(EXPORT_PERIOD_DAYS),
        date_to: today,
      })
    },
  })
}
