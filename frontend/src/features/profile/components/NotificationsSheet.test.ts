// @vitest-environment jsdom
/**
 * Окно «Уведомления» целиком, как его видит человек: открыли → строка → клик.
 *
 * Библиотеки тестирования компонентов в проекте нет, поэтому окно рисуется
 * настоящим React в jsdom, а сервер подменён: проверяем, какие запросы уходят
 * и куда переходит приложение.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppNotification } from '@/entities/types'

const auth = vi.hoisted(() => ({ isAdmin: true }))
vi.mock('@/shared/hooks/useAuth', () => ({ useAuth: () => ({ isAdmin: auth.isAdmin }) }))

import { NotificationsSheet } from './NotificationsSheet'

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function note(patch: Partial<AppNotification> & { id: number }): AppNotification {
  return {
    kind: 'deal_paid',
    entity_type: 'deal',
    entity_id: 4,
    title: 'Оплата получена',
    text: 'DEAL-4 · 10,00 ₽ · Wassup',
    read_at: null,
    created_at: '2026-10-10T09:00:00Z',
    ...patch,
  }
}

interface Call {
  method: string
  path: string
  body: unknown
}

let calls: Call[]
let items: AppNotification[]
let unread: number
let root: Root
let host: HTMLElement
let closed: number
let location = ''

function Probe(): ReactElement {
  location = useLocation().pathname
  return createElement('span')
}

function respond(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  items = []
  unread = 0
  closed = 0
  location = ''
  auth.isAdmin = true
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, path: url.pathname + url.search, body })
      if (url.pathname === '/api/v1/notifications/unread-count') return respond({ count: unread })
      if (url.pathname === '/api/v1/notifications/read') return respond({ updated: 1 })
      if (url.pathname === '/api/v1/notifications') return respond({ items, next_cursor: null })
      return respond({})
    }),
  )
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

async function settle() {
  // Запросы react-query и анимации окна заканчиваются за несколько кругов событий.
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

async function show(open: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          MemoryRouter,
          { initialEntries: ['/chats'] },
          createElement(Probe),
          createElement(NotificationsSheet, {
            open,
            onOpenChange: (next: boolean) => {
              if (!next) closed += 1
            },
          }),
        ),
      ),
    )
  })
  await settle()
}

function rowButton(title: string): HTMLButtonElement | null {
  const buttons = Array.from(document.body.querySelectorAll('button'))
  return (buttons.find((b) => b.textContent?.includes(title)) as HTMLButtonElement) ?? null
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
  await settle()
}

describe('NotificationsSheet', () => {
  it('закрытое окно ничего не запрашивает', async () => {
    await show(false)
    expect(calls.filter((c) => c.path.startsWith('/api/v1/notifications?'))).toHaveLength(0)
  })

  it('открытое окно само запрашивает список и показывает заголовок один раз', async () => {
    items = [note({ id: 1 })]
    await show(true)
    expect(calls.some((c) => c.path.startsWith('/api/v1/notifications?limit=30'))).toBe(true)
    const titles = Array.from(document.body.querySelectorAll('h2')).filter(
      (h) => h.textContent === 'Уведомления',
    )
    expect(titles).toHaveLength(1)
    expect(document.body.textContent).toContain('Оплата получена')
  })

  it('клик по оплате: помечает прочитанной, закрывает окно и ведёт в сделку', async () => {
    items = [note({ id: 7, entity_type: 'deal', entity_id: 4 })]
    unread = 1
    await show(true)

    const row = rowButton('Оплата получена')
    expect(row).not.toBeNull()
    await click(row as HTMLButtonElement)

    expect(calls).toContainEqual({
      method: 'POST',
      path: '/api/v1/notifications/read',
      body: { ids: [7] },
    })
    expect(closed).toBe(1)
    expect(location).toBe('/payments/4')
  })

  it('уже прочитанное уведомление открывается без повторной пометки', async () => {
    items = [note({ id: 8, read_at: '2026-10-10T10:00:00Z' })]
    await show(true)
    await click(rowButton('Оплата получена') as HTMLButtonElement)

    expect(calls.some((c) => c.path === '/api/v1/notifications/read')).toBe(false)
    expect(location).toBe('/payments/4')
  })

  it('аккаунт: руководителя ведёт в «Аккаунты»', async () => {
    items = [
      note({
        id: 9,
        kind: 'account_assigned',
        entity_type: 'account',
        entity_id: 3,
        title: 'Вас назначили на аккаунт',
      }),
    ]
    await show(true)
    await click(rowButton('Вас назначили на аккаунт') as HTMLButtonElement)
    expect(location).toBe('/profile/accounts')
  })

  it('аккаунт: менеджера ведёт в «Профиль»', async () => {
    auth.isAdmin = false
    items = [
      note({
        id: 10,
        kind: 'account_error',
        entity_type: 'account',
        entity_id: 3,
        title: 'Аккаунт потерял связь',
      }),
    ]
    await show(true)
    await click(rowButton('Аккаунт потерял связь') as HTMLButtonElement)
    expect(location).toBe('/profile')
  })

  it('уведомление без объекта — обычный текст, а не кнопка', async () => {
    items = [
      note({
        id: 11,
        kind: 'account_error',
        entity_type: null,
        entity_id: null,
        title: 'Что-то без ссылки',
      }),
    ]
    await show(true)
    expect(document.body.textContent).toContain('Что-то без ссылки')
    expect(rowButton('Что-то без ссылки')).toBeNull()
  })

  it('«Отметить все» отправляет пометку всех и показывается, только если есть непрочитанные', async () => {
    items = [note({ id: 12 })]
    unread = 1
    await show(true)
    const all = rowButton('Отметить все прочитанными')
    expect(all).not.toBeNull()
    await click(all as HTMLButtonElement)
    expect(calls).toContainEqual({ method: 'POST', path: '/api/v1/notifications/read', body: {} })
  })

  it('все прочитаны — кнопки «Отметить все» нет', async () => {
    items = [note({ id: 13, read_at: '2026-10-10T10:00:00Z' })]
    unread = 0
    await show(true)
    expect(rowButton('Отметить все прочитанными')).toBeNull()
  })

  it('свёрнутый список показывает три строки и раскрывается', async () => {
    items = [1, 2, 3, 4, 5].map((id) => note({ id, title: `Событие ${id}` }))
    await show(true)
    expect(document.body.textContent).toContain('Событие 3')
    expect(document.body.textContent).not.toContain('Событие 4')
    await click(rowButton('Развернуть все') as HTMLButtonElement)
    expect(document.body.textContent).toContain('Событие 5')
  })
})
