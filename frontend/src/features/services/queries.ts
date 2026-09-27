/**
 * Справочник услуг: чтение — для окна оплаты у всех, правка — у руководителя.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import type { Service, ServiceSuggestion } from '@/entities/types'
import { api } from '@/shared/api/client'

export interface ServiceInput {
  name: string
  price: number | null
  description: string | null
  is_active: boolean
  sort_order?: number
}

/** Активные услуги — то, что предлагается в окне оплаты. */
export function useServices() {
  return useQuery({
    queryKey: ['services', 'active'],
    queryFn: ({ signal }) => api.get<Service[]>('/services', undefined, signal),
    staleTime: 60_000,
  })
}

/** Все, включая снятые с продажи, — для справочника руководителя. */
export function useAdminServices() {
  return useQuery({
    queryKey: ['services', 'admin'],
    queryFn: ({ signal }) => api.get<Service[]>('/services', { include_inactive: true }, signal),
  })
}

export function useServiceSuggestions(enabled: boolean) {
  return useQuery({
    queryKey: ['services', 'suggestions'],
    queryFn: ({ signal }) => api.get<ServiceSuggestion[]>('/services/suggestions', undefined, signal),
    enabled,
  })
}

function invalidate(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['services'] })
}

export function useCreateService() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ServiceInput) => api.post<Service>('/services', input),
    onSuccess: () => invalidate(queryClient),
  })
}

export function useUpdateService() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...input }: Partial<ServiceInput> & { id: number }) =>
      api.patch<Service>(`/services/${id}`, input),
    onSuccess: () => invalidate(queryClient),
  })
}

export function useDeleteService() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => api.del<void>(`/services/${id}`),
    onSuccess: () => invalidate(queryClient),
  })
}
