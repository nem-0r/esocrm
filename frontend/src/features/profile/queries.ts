/**
 * Запросы раздела «Профиль»: свой профиль, аккаунты, сотрудники, уведомления,
 * настройки системы.
 *
 * Ключи кэша плоские, как в «Чатах». Мутации инвалидируют ровно то, что задели:
 * ключ ['accounts'] покрывает и список, и сводку — они всегда меняются вместе.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type {
  Account,
  AccountSummary,
  AppNotification,
  CursorPage,
  FunnelStage,
  Me,
  Settings,
  StaffMember,
  StatsOverview,
  UserRole,
} from '@/entities/types'
import { api } from '@/shared/api/client'
import { isoDate } from '@/shared/lib/format'

/** Списки сервер отдаёт либо массивом, либо курсорной страницей. */
type List<T> = T[] | CursorPage<T>

function unwrap<T>(payload: List<T>): T[] {
  return Array.isArray(payload) ? payload : payload.items
}

/* ------------------------------------------------------------------ профиль */

export interface UpdateMeInput {
  accepting_leads?: boolean
  current_password?: string
  new_password?: string
}

export function useUpdateMe() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: UpdateMeInput) => api.patch<Me>('/auth/me', input),
    onSuccess: (me) => {
      queryClient.setQueryData(['me'], me)
    },
  })
}

/**
 * Показатели за текущий календарный месяц.
 * `retry: false` — если раздела статистики ещё нет, блок просто не рисуется,
 * вместо честного отсутствия данных нулей не показываем.
 */
export function useMonthOverview() {
  const now = new Date()
  const from = isoDate(new Date(now.getFullYear(), now.getMonth(), 1))
  const to = isoDate(now)
  return useQuery({
    queryKey: ['stats', 'overview', from, to],
    queryFn: ({ signal }) => api.get<StatsOverview>('/stats/overview', { date_from: from, date_to: to }, signal),
    retry: false,
    staleTime: 5 * 60_000,
  })
}

/* ----------------------------------------------------------------- аккаунты */

/** Подтяжка переписки из Telegram. Ответ означает «принято в работу»:
 *  сама загрузка идёт на шлюзе и на большом аккаунте занимает минуты. */
export function syncAccountHistory(accountId: number) {
  return api.post<{ started: boolean; since: string }>(`/accounts/${accountId}/sync-history`, {})
}

export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: async ({ signal }) => unwrap(await api.get<List<Account>>('/accounts', {}, signal)),
  })
}

export function useAccountsSummary() {
  return useQuery({
    queryKey: ['accounts', 'summary'],
    queryFn: ({ signal }) => api.get<AccountSummary>('/accounts/summary', {}, signal),
  })
}

export interface CreateAccountInput {
  title: string
  phone: string
  funnel_stage: FunnelStage
}

export function useCreateAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateAccountInput) => api.post<Account>('/accounts', input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['accounts'] }),
  })
}

/** Демо-режим отвечает `demo: true` и подсказкой, которую показываем дословно. */
export interface SendCodeResult {
  /** Идентификатор запроса кода: его надо вернуть на шаге подтверждения. */
  phone_code_hash: string
  demo?: boolean
  hint?: string
  sent_to?: 'app' | 'sms'
}

export function useSendCode() {
  return useMutation({
    mutationFn: (accountId: number) =>
      api.post<SendCodeResult>(`/accounts/${accountId}/send-code`),
  })
}

export interface ConfirmCodeResult {
  needs_password?: boolean
  account?: Account
}

export function useConfirmCode() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: {
      accountId: number
      code: string
      phoneCodeHash: string
      password?: string
    }) =>
      api.post<ConfirmCodeResult>(`/accounts/${input.accountId}/confirm-code`, {
        code: input.code,
        // Идентификатор запроса кода: сервер связывает по нему код и попытку входа.
        phone_code_hash: input.phoneCodeHash,
        password: input.password,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['accounts'] }),
  })
}

export function useSetAccountManagers() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { accountId: number; userIds: number[]; notify: boolean }) =>
      api.post<Account>(`/accounts/${input.accountId}/managers`, {
        user_ids: input.userIds,
        notify: input.notify,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['staff'] })
    },
  })
}

export interface UpdateAccountInput {
  title?: string
  funnel_stage?: FunnelStage
  is_active?: boolean
}

export function useUpdateAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { accountId: number; patch: UpdateAccountInput }) =>
      api.patch<Account>(`/accounts/${input.accountId}`, input.patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['accounts'] }),
  })
}

export function useDeleteAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (accountId: number) => api.del<void>(`/accounts/${accountId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      queryClient.invalidateQueries({ queryKey: ['staff'] })
    },
  })
}

/* --------------------------------------------------------------- сотрудники */

export type StaffStatusFilter = 'online' | 'offline' | 'no_account'

export interface StaffFilters {
  q?: string
  status?: StaffStatusFilter | null
}

export function useStaff(filters: StaffFilters = {}) {
  const status = filters.status ?? null
  const q = filters.q?.trim() ?? ''
  return useQuery({
    queryKey: ['staff', q, status],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.get<List<StaffMember>>(
          '/users',
          {
            q: q || undefined,
            status: status === 'online' || status === 'offline' ? status : undefined,
            no_account: status === 'no_account' ? true : undefined,
            limit: 100,
          },
          signal,
        ),
      ),
  })
}

export function useStaffMember(id: number | null) {
  return useQuery({
    queryKey: ['staff', 'member', id],
    queryFn: ({ signal }) => api.get<StaffMember>(`/users/${id}`, {}, signal),
    enabled: id !== null,
  })
}

export interface InviteStaffInput {
  full_name: string
  email: string
  phone?: string | null
  role: UserRole
  account_ids: number[]
}

/** Почта пока не отправляется — сервер возвращает ссылку, её показываем руководителю. */
export type InvitedStaff = StaffMember & { invite_url: string }

export function useInviteStaff() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: InviteStaffInput) => api.post<InvitedStaff>('/users', input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['staff'] }),
  })
}

export interface UpdateStaffInput {
  full_name?: string
  phone?: string | null
  role?: UserRole
  is_active?: boolean
  account_ids?: number[]
  /** График работы (ТЗ Б.3). Ставит только руководитель. */
  schedule?: { enabled: boolean; days: number[]; start: string; end: string }
}

export function useUpdateStaff() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: { userId: number; patch: UpdateStaffInput }) =>
      api.patch<StaffMember>(`/users/${input.userId}`, input.patch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['staff'] })
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      // Руководитель может поменять график себе — тогда обновляется и свой профиль.
      queryClient.invalidateQueries({ queryKey: ['me'] })
    },
  })
}

export function useResendInvite() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (userId: number) => api.post<{ invite_url: string }>(`/users/${userId}/resend-invite`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['staff'] }),
  })
}

/* -------------------------------------------------------------- уведомления */

export function useNotifications(limit = 10) {
  return useQuery({
    queryKey: ['notifications', limit],
    queryFn: async ({ signal }) =>
      unwrap(await api.get<List<AppNotification>>('/notifications', { limit }, signal)),
  })
}

export function useUnreadCount() {
  return useQuery({
    queryKey: ['notifications', 'unread-count'],
    queryFn: ({ signal }) => api.get<{ count: number }>('/notifications/unread-count', {}, signal),
    refetchInterval: 60_000,
  })
}

/** Без списка идентификаторов сервер помечает прочитанными все уведомления. */
export function useMarkNotificationsRead() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (ids?: number[]) => api.post<void>('/notifications/read', ids ? { ids } : {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['notifications'] }),
  })
}

/* --------------------------------------------------------------- настройки */

export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => api.get<Settings>('/settings', {}, signal),
  })
}

export function useUpdateSettings() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: Partial<Settings>) => api.patch<Settings>('/settings', input),
    onSuccess: (settings) => {
      queryClient.setQueryData(['settings'], settings)
      queryClient.invalidateQueries({ queryKey: ['counters'] })
    },
  })
}
