/**
 * Запросы раздела «Оплаты».
 *
 * Ключи плоские: ['deals', ...] — список и сводка, ['deal', id] — карточка.
 * Поэтому одна инвалидация ['deals'] обновляет и список, и плитки, а карточка
 * обновляется точечно ответом мутации.
 *
 * Деньги везде целые копейки: рубли появляются только в форматере.
 */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import type {
  CursorPage,
  DealCard,
  DealRow,
  DealStatus,
  DealsSummary,
  PaymentMethod,
  Requisite,
} from '@/entities/types'
import { api, downloadFile } from '@/shared/api/client'

export interface DealFilters {
  dateFrom?: string | null
  dateTo?: string | null
  status?: DealStatus | null
  clientId?: number | null
  accountId?: number | null
  conversationId?: number | null
}

/** Фильтры по дате, статусу и клиенту работают одновременно — сервер их и ждёт вместе. */
function toQuery(filters: DealFilters) {
  return {
    date_from: filters.dateFrom ?? undefined,
    date_to: filters.dateTo ?? undefined,
    status: filters.status ?? undefined,
    client_id: filters.clientId ?? undefined,
    account_id: filters.accountId ?? undefined,
    conversation_id: filters.conversationId ?? undefined,
  }
}

function filterKey(filters: DealFilters): (string | number | null)[] {
  return [
    filters.dateFrom ?? null,
    filters.dateTo ?? null,
    filters.status ?? null,
    filters.clientId ?? null,
    filters.accountId ?? null,
    filters.conversationId ?? null,
  ]
}

export function useDeals(filters: DealFilters) {
  return useInfiniteQuery({
    queryKey: ['deals', 'list', ...filterKey(filters)],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api.get<CursorPage<DealRow>>(
        '/deals',
        { ...toQuery(filters), cursor: pageParam ?? undefined, limit: 30 },
        signal,
      ),
    getNextPageParam: (last) => last.next_cursor,
  })
}

export function useDealsSummary(filters: DealFilters) {
  return useQuery({
    queryKey: ['deals', 'summary', ...filterKey(filters)],
    queryFn: ({ signal }) => api.get<DealsSummary>('/deals/summary', toQuery(filters), signal),
  })
}

export function useDeal(id: number | null) {
  return useQuery({
    queryKey: ['deal', id],
    queryFn: () => api.get<DealCard>(`/deals/${id}`),
    enabled: id !== null,
  })
}

export interface RequisiteIncome {
  requisite_id: number | null
  title: string
  country: string | null
  amount: number
  count: number
}

/** Поступления по реквизитам за период — для сверки с банковской выпиской.
 *  Запрашивается только когда руководитель раскрыл блок: запрос не из дешёвых,
 *  а нужен он далеко не в каждом заходе на экран оплат. */
export function useDealsByRequisite(filters: DealFilters, enabled: boolean) {
  return useQuery({
    queryKey: ['deals', 'by-requisite', filters],
    queryFn: ({ signal }) =>
      api.get<RequisiteIncome[]>('/deals/by-requisite', toQuery(filters), signal),
    enabled,
    staleTime: 60_000,
  })
}

export function useRequisites() {
  return useQuery({
    queryKey: ['requisites'],
    queryFn: () => api.get<Requisite[]>('/requisites'),
    staleTime: 5 * 60_000,
  })
}

export interface DealItemInput {
  name: string
  amount: number
}

export interface CreateDealInput {
  conversation_id: number
  payment_method: PaymentMethod
  /** Обязателен для оплаты по реквизитам; у ссылки счёта нет — деньги идут в Робокассу. */
  requisite_id?: number
  items: DealItemInput[]
  /** Текст, которым менеджер сопровождает счёт (ТЗ п. 4.4). */
  intro_text?: string
}

export interface UpdateDealInput {
  items?: DealItemInput[]
  requisite_id?: number
  comment: string
}

/**
 * Создание оплаты из чата: POST /deals, затем POST /deals/{id}/send.
 * Если счёт не ушёл в чат, черновик всё равно остался — говорим об этом прямо,
 * а не делаем вид, что ничего не произошло.
 */
export function useCreateAndSendDeal(conversationId: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: CreateDealInput) => {
      const draft = await api.post<DealCard>('/deals', input)
      try {
        return await api.post<DealCard>(`/deals/${draft.id}/send`)
      } catch (cause) {
        const reason = cause instanceof Error ? cause.message : 'сервер не ответил'
        throw new Error(
          `Черновик ${draft.number} создан, но счёт не ушёл в чат: ${reason}. ` +
            'Отправьте его из раздела «Оплаты».',
        )
      }
    },
    // Черновик появляется в списке даже при неудачной отправке — обновляем в любом случае.
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['messages', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['deals'] })
      queryClient.invalidateQueries({ queryKey: ['counters'] })
    },
  })
}

/** Общая часть действий над сделкой: ответ сервера кладём в кэш карточки. */
function useDealAction<TVariables>(
  dealId: number,
  conversationId: number | null,
  mutationFn: (variables: TVariables) => Promise<DealCard>,
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: (deal) => {
      queryClient.setQueryData(['deal', dealId], deal)
      queryClient.invalidateQueries({ queryKey: ['deals'] })
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
      queryClient.invalidateQueries({ queryKey: ['counters'] })
      if (conversationId !== null) {
        queryClient.invalidateQueries({ queryKey: ['messages', conversationId] })
        queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      }
    },
  })
}

export function useSendDeal(dealId: number, conversationId: number | null) {
  return useDealAction<void>(dealId, conversationId, () =>
    api.post<DealCard>(`/deals/${dealId}/send`),
  )
}

export interface PayDealInput {
  /** ТЗ п. 6.3: без номера чека сервер не подтвердит оплату. */
  receipt_number: string
  /** Куда деньги пришли фактически. Пусто — на реквизит из счёта. */
  paid_to_requisite_id?: number | null
}

export function usePayDeal(dealId: number, conversationId: number | null) {
  return useDealAction<PayDealInput>(dealId, conversationId, (input) =>
    api.post<DealCard>(`/deals/${dealId}/pay`, input),
  )
}

export function useUpdateDeal(dealId: number, conversationId: number | null) {
  return useDealAction<UpdateDealInput>(dealId, conversationId, (input) =>
    api.patch<DealCard>(`/deals/${dealId}`, input),
  )
}

export function useCancelDeal(dealId: number, conversationId: number | null) {
  return useDealAction<{ reason: string }>(dealId, conversationId, (input) =>
    api.post<DealCard>(`/deals/${dealId}/cancel`, input),
  )
}

/** Выгрузка отчёта. Скачивание идёт через сервер: права проверяются там же. */
export function exportDeals(filters: DealFilters) {
  const from = filters.dateFrom ?? 'all'
  const to = filters.dateTo ?? 'all'
  return downloadFile('/deals/export', `oplaty-${from}-${to}.csv`, toQuery(filters))
}
