import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import type { Settings } from '@/entities/types'
import { api } from '@/shared/api/client'
import { todayInZone } from '@/shared/lib/period'

/**
 * Сегодняшняя дата «ГГГГ-ММ-ДД» по часовому поясу организации — так же, как её считает
 * сервер для периодов «по умолчанию». Настройки читает любой сотрудник (GET /settings),
 * ключ запроса тот же, что у `useSettings` в профиле, поэтому кэш общий. Пока настройки
 * не пришли, берётся пояс браузера. Дата пересчитывается раз в минуту: страница,
 * оставленная открытой на ночь, к утру не застрянет во «вчера».
 */
export function useOrgToday(): string {
  const settings = useQuery({
    queryKey: ['settings'],
    queryFn: ({ signal }) => api.get<Settings>('/settings', {}, signal),
    staleTime: 5 * 60_000,
  })
  const zone = settings.data?.timezone
  const [today, setToday] = useState(() => todayInZone(zone))

  useEffect(() => {
    const refresh = () => setToday(todayInZone(zone))
    refresh()
    const timer = window.setInterval(refresh, 60_000)
    return () => window.clearInterval(timer)
  }, [zone])

  return today
}
