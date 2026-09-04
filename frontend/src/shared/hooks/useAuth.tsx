/**
 * Текущий пользователь и права.
 *
 * Права здесь — только для того, чтобы не показывать бесполезные кнопки.
 * Настоящая проверка живёт на сервере: скрытая кнопка не защита.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, type ReactNode } from 'react'

import type { Me } from '@/entities/types'
import { ApiError, api, setUnauthorizedHandler } from '@/shared/api/client'
import { realtime } from '@/shared/api/ws'

interface AuthValue {
  me: Me | null
  isLoading: boolean
  error: unknown
  isAdmin: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()

  const {
    data: me,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['me'],
    // 401 — это не сбой, а ответ «сессии нет». Иначе экран входа уходит
    // в бесконечный цикл: ошибка чистит кэш, кэш перезапрашивает, и по кругу.
    queryFn: async ({ signal }) => {
      try {
        return await api.probe<Me>('/auth/me', signal)
      } catch (cause) {
        if (cause instanceof ApiError && cause.isUnauthorized) return null
        throw cause
      }
    },
    retry: false,
    staleTime: 60_000,
  })

  // Сессия истекла — очищаем кэш и уводим на вход. Молча показывать старые
  // данные нельзя: пользователь должен понимать, что он больше не авторизован.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      // Сессия истекла посреди работы: помечаем выход и убираем данные разделов.
      // Сам запрос ['me'] не трогаем — он вернёт null сам и без повторов.
      queryClient.setQueryData(['me'], null)
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== 'me',
      })
      realtime.disconnect()
    })
    return () => setUnauthorizedHandler(null)
  }, [queryClient])

  // Зависимость — идентификатор, а не объект: перезапрос профиля даёт новый
  // объект при том же пользователе, и соединение пересоздавалось бы вхолостую.
  const userId = me?.id ?? null

  useEffect(() => {
    if (userId !== null) realtime.connect()
    else realtime.disconnect()
  }, [userId])

  // Отметка присутствия: «более 5 минут отсутствия — офлайн».
  useEffect(() => {
    if (userId === null) return
    const beat = () => void api.post('/auth/heartbeat').catch(() => undefined)
    beat()
    const timer = setInterval(beat, 60_000)
    return () => clearInterval(timer)
  }, [userId])

  const login = useCallback(
    async (email: string, password: string) => {
      await api.post('/auth/login', { email, password })
      await refetch()
    },
    [refetch],
  )

  const logout = useCallback(async () => {
    try {
      await api.post('/auth/logout')
    } finally {
      queryClient.setQueryData(['me'], null)
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== 'me' })
      realtime.disconnect()
    }
  }, [queryClient])

  const value: AuthValue = {
    me: me ?? null,
    isLoading,
    error,
    isAdmin: me?.role === 'admin',
    login,
    logout,
    refresh: () => void refetch(),
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth вызван вне AuthProvider')
  return value
}

/** Пользователь, гарантированно авторизованный — внутри защищённых экранов. */
export function useMe(): Me {
  const { me } = useAuth()
  if (!me) throw new Error('useMe вызван вне защищённого маршрута')
  return me
}
