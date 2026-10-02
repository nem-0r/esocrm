/**
 * Состояние сервера для руководителя: диск, шлюз, план ресурсов.
 * Менеджеру недоступно — запрос включается только для руководителя.
 */

import { useQuery } from '@tanstack/react-query'

import type { SystemStatus } from '@/entities/types'
import { api } from '@/shared/api/client'

export function useSystemStatus(enabled: boolean) {
  return useQuery({
    queryKey: ['system', 'status'],
    queryFn: ({ signal }) => api.get<SystemStatus>('/system/status', undefined, signal),
    enabled,
    // Раз в минуту: диск и очередь меняются медленно, а тревога должна появиться быстро.
    refetchInterval: 60_000,
    staleTime: 30_000,
    // Сбой самого запроса баннером не показываем: сервер без ответа — это уже
    // видно по плашке «Нет связи с сервером».
    retry: false,
  })
}
