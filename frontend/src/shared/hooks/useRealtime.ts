/**
 * Подписка экранов на живые события и статус связи.
 */

import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import type { WsEvent } from '@/entities/types'
import { realtime, type WsStatus } from '@/shared/api/ws'

/** Подписка на конкретный тип события. */
export function useWsEvent<T extends WsEvent['type']>(
  type: T,
  handler: (data: Extract<WsEvent, { type: T }> extends { data: infer D } ? D : never) => void,
) {
  useEffect(() => {
    const unsubscribe = realtime.subscribe((event) => {
      if (event.type !== type) return
      const payload = (event as { data?: unknown }).data
      handler(payload as never)
    })
    return () => {
      unsubscribe()
    }
  })
}

export function useWsStatus(): WsStatus {
  const [status, setStatus] = useState<WsStatus>(realtime.status)
  useEffect(() => {
    const unsubscribe = realtime.onStatus(setStatus)
    return () => {
      unsubscribe()
    }
  }, [])
  return status
}

/**
 * Общая перерисовка списков по живым событиям.
 * Подключается один раз в оболочке приложения.
 */
export function useRealtimeSync() {
  const queryClient = useQueryClient()
  const status = useWsStatus()

  useEffect(() => {
    const unsubscribe = realtime.subscribe((event) => {
      switch (event.type) {
        case 'message.new':
        case 'message.updated':
          queryClient.invalidateQueries({
            queryKey: ['messages', event.data.conversation_id],
          })
          queryClient.invalidateQueries({ queryKey: ['conversations'] })
          break
        case 'conversation.updated':
          queryClient.invalidateQueries({ queryKey: ['conversations'] })
          break
        case 'counters.updated':
          // Событие — сигнал перезапросить, не готовые числа: счётчики зависят
          // от прав получателя (backend/app/services/conversation_service.py).
          // setQueryData здесь затирал бы кэш телом события {conversation_id,
          // stale}, и счётчики на экране на секунду показывали бы 0 всем.
          queryClient.invalidateQueries({ queryKey: ['counters'] })
          break
        case 'deal.updated':
          queryClient.invalidateQueries({ queryKey: ['deals'] })
          queryClient.invalidateQueries({ queryKey: ['conversations'] })
          break
        case 'notification.new':
          queryClient.invalidateQueries({ queryKey: ['notifications'] })
          break
        case 'account.status':
          queryClient.invalidateQueries({ queryKey: ['accounts'] })
          break
        case 'presence.updated':
          queryClient.invalidateQueries({ queryKey: ['staff'] })
          break
      }
    })
    return () => {
      unsubscribe()
    }
  }, [queryClient])

  // Связь восстановилась — дозагружаем пропущенное, а не показываем устаревшее.
  // Обновляем только данные разделов: перезапрос профиля пересоздал бы
  // соединение, а это снова «связь восстановилась» — и так по кругу.
  const wasOffline = useRef(false)
  useEffect(() => {
    if (status === 'offline') {
      wasOffline.current = true
      return
    }
    if (status === 'online' && wasOffline.current) {
      wasOffline.current = false
      queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] !== 'me',
      })
    }
  }, [status, queryClient])

  return status
}
