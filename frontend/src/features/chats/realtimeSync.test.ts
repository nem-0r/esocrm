// @vitest-environment jsdom
/**
 * Живые события и перерисовка раздела «Чаты» (`useRealtimeSync`): обрыв связи аккаунта обязан
 * дойти до открытого чата, а смена статуса сообщения — до значка в списке.
 *
 * Соединение подменено: тест сам «присылает» события и смотрит, какие запросы кэша обновились.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WsEvent } from '@/entities/types'

const bus = vi.hoisted(() => ({ handlers: new Set<(event: unknown) => void>() }))
vi.mock('@/shared/api/ws', () => ({
  realtime: {
    status: 'online',
    subscribe: (handler: (event: unknown) => void) => {
      bus.handlers.add(handler)
      return () => bus.handlers.delete(handler)
    },
    onStatus: (handler: (status: string) => void) => {
      handler('online')
      return () => undefined
    },
  },
}))

import { useRealtimeSync } from '@/shared/hooks/useRealtime'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let host: HTMLElement
let root: Root
let client: QueryClient

function Sync() {
  useRealtimeSync()
  return null
}

function emit(event: WsEvent) {
  bus.handlers.forEach((handler) => handler(event))
}

/** Какие ключи кэша объявлены устаревшими после события. */
function invalidatedBy(event: WsEvent): string[] {
  const spy = vi.spyOn(client, 'invalidateQueries')
  emit(event)
  const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
  spy.mockRestore()
  return keys
}

beforeEach(async () => {
  bus.handlers.clear()
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  client = new QueryClient()
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client }, createElement(Sync)))
  })
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
})

describe('account.status', () => {
  const event = { type: 'account.status', data: { account_id: 2, status: 'error', reason: 'Сессия отозвана' } } as WsEvent

  it('обновляет карточку открытого чата, список чатов и список аккаунтов', () => {
    const keys = invalidatedBy(event)
    // Префикс ['conversation'] покрывает ['conversation', <id>] любого открытого чата
    expect(keys).toContain(JSON.stringify(['conversation']))
    expect(keys).toContain(JSON.stringify(['conversations']))
    expect(keys).toContain(JSON.stringify(['accounts']))
  })

  it('действительно задевает кэш открытого чата: он помечается устаревшим', () => {
    client.setQueryData(['conversation', 7], { id: 7 })
    emit(event)
    expect(client.getQueryState(['conversation', 7])?.isInvalidated).toBe(true)
  })
})

describe('сообщения', () => {
  it('новое и изменённое сообщение обновляют ленту чата и список (значок статуса в превью)', () => {
    for (const type of ['message.new', 'message.updated'] as const) {
      const keys = invalidatedBy({ type, data: { conversation_id: 7 } } as WsEvent)
      expect(keys, type).toContain(JSON.stringify(['messages', 7]))
      expect(keys, type).toContain(JSON.stringify(['conversations']))
    }
  })
})
