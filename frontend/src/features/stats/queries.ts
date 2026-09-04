import { useQuery } from '@tanstack/react-query'

import type { Granularity, ManagerStats, StatsOverview, StatsSeries } from '@/entities/types'
import { api } from '@/shared/api/client'

export interface StatsRange {
  from: string
  to: string
}

export function useOverview(range: StatsRange, userId?: number | null) {
  return useQuery({
    queryKey: ['stats', 'overview', range.from, range.to, userId ?? null],
    queryFn: ({ signal }) =>
      api.get<StatsOverview>(
        '/stats/overview',
        { date_from: range.from, date_to: range.to, user_id: userId ?? undefined },
        signal,
      ),
  })
}

export function useSeries(range: StatsRange, granularity: Granularity, userId?: number | null) {
  return useQuery({
    queryKey: ['stats', 'series', range.from, range.to, granularity, userId ?? null],
    queryFn: ({ signal }) =>
      api.get<StatsSeries>(
        '/stats/series',
        {
          date_from: range.from,
          date_to: range.to,
          granularity,
          user_id: userId ?? undefined,
        },
        signal,
      ),
  })
}

export function useManagerStats(range: StatsRange, enabled: boolean) {
  return useQuery({
    queryKey: ['stats', 'managers', range.from, range.to],
    queryFn: ({ signal }) =>
      api.get<ManagerStats[]>(
        '/stats/managers',
        { date_from: range.from, date_to: range.to },
        signal,
      ),
    enabled,
  })
}

/** Дни недоступны на длинном периоде — сервер их отвергает, и это правило с доски. */
export const MAX_DAYS_FOR_DAILY = 62

export function daysBetween(from: string, to: string): number {
  return Math.round(
    (new Date(`${to}T00:00:00`).getTime() - new Date(`${from}T00:00:00`).getTime()) / 86_400_000,
  )
}
