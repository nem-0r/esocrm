// @vitest-environment jsdom
/**
 * Колокольчик и поиск в шапке раздела — настоящие, без подмен (BUG-08).
 *
 * В других тестах `SectionActions` заменён пустышкой, поэтому здесь проверяется то, что
 * иначе осталось бы без присмотра: колокольчик открывает окно «Уведомления» на любой
 * странице, не уводя человека с неё; бейдж и подпись для диктора показывают число
 * непрочитанных; окно закрывается, а клик по строке ведёт к объекту. Окно уведомлений тоже
 * настоящее, сервер подменён.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AppNotification } from '@/entities/types'

vi.mock('@/shared/hooks/useAuth', () => ({ useAuth: () => ({ isAdmin: true }) }))

import { SectionActions } from './SectionActions'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  body: unknown
}

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

let calls: Call[]
let items: AppNotification[]
let unread: number
let root: Root
let host: HTMLElement
let location = ''

function Probe(): ReactElement {
  const here = useLocation()
  location = here.pathname
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
  location = ''
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method: init?.method ?? 'GET', path: url.pathname + url.search, body })
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
  for (let i = 0; i < 6; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

/** Шапка раздела на странице `/payments` — не на профиле: окно обязано открыться прямо здесь. */
async function show(path = '/payments') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          MemoryRouter,
          { initialEntries: [path] },
          createElement(Probe),
          createElement(SectionActions),
        ),
      ),
    )
  })
  await settle()
}

async function click(el: Element) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
  await settle()
}

const bell = () =>
  Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).find((b) =>
    b.getAttribute('aria-label')?.startsWith('Уведомления'),
  ) as HTMLButtonElement
const dialog = () => document.body.querySelector('[role="dialog"]')
const feedCalls = () =>
  calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/v1/notifications?'))

describe('SectionActions: колокольчик', () => {
  it('нет непрочитанных: подпись «Уведомления», бейджа нет, окно закрыто и ничего не запрашивает', async () => {
    await show()
    expect(bell().getAttribute('aria-label')).toBe('Уведомления')
    expect(bell().querySelector('span')).toBeNull()
    expect(dialog()).toBeNull()
    expect(feedCalls()).toHaveLength(0)
  })

  it('непрочитанные: число в бейдже и в подписи для диктора', async () => {
    unread = 3
    await show()
    expect(bell().getAttribute('aria-label')).toBe('Уведомления, непрочитанных: 3')
    expect(bell().textContent).toBe('3')
  })

  it('больше девяти — «9+», а точное число остаётся в подписи', async () => {
    unread = 12
    await show()
    expect(bell().textContent).toBe('9+')
    expect(bell().getAttribute('aria-label')).toBe('Уведомления, непрочитанных: 12')
  })

  it('клик открывает окно «Уведомления» прямо на этой странице, не уводя с неё', async () => {
    items = [note({ id: 1, title: 'Оплата получена' })]
    unread = 1
    await show('/payments')
    expect(bell().getAttribute('aria-haspopup')).toBe('dialog')

    await click(bell())

    expect(dialog()).not.toBeNull()
    expect(location).toBe('/payments')
    const title = document.body.querySelector('h2')
    expect(title?.textContent).toBe('Уведомления')
    expect(feedCalls().length).toBeGreaterThanOrEqual(1)
    expect(document.body.textContent).toContain('Оплата получена')
  })

  it('окно открывается и на странице, где колокольчик не в профиле (чаты, статистика)', async () => {
    items = [note({ id: 1 })]
    for (const path of ['/chats', '/stats', '/clients']) {
      await act(async () => root.unmount())
      root = createRoot(host)
      await show(path)
      await click(bell())
      expect(dialog(), path).not.toBeNull()
      expect(location).toBe(path)
    }
  })

  it('крестик закрывает окно, остаёмся на той же странице', async () => {
    items = [note({ id: 1 })]
    await show()
    await click(bell())
    expect(dialog()).not.toBeNull()

    await click(document.body.querySelector('[aria-label="Закрыть"]') as Element)
    expect(dialog()).toBeNull()
    expect(location).toBe('/payments')
  })

  it('Escape закрывает окно, и его можно открыть снова', async () => {
    items = [note({ id: 1 })]
    await show()
    await click(bell())
    // Как в браузере: клавиша приходит в окно, где стоит фокус.
    await act(async () => {
      dialog()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    await settle()
    expect(dialog()).toBeNull()

    await click(bell())
    expect(dialog()).not.toBeNull()
  })

  it('клик по строке: помечает прочитанной, закрывает окно и ведёт в сделку', async () => {
    items = [note({ id: 7, entity_type: 'deal', entity_id: 4 })]
    unread = 1
    await show('/chats')
    await click(bell())

    const row = Array.from(document.body.querySelectorAll('li button')).find((b) =>
      b.textContent?.includes('Оплата получена'),
    ) as HTMLButtonElement
    await click(row)

    expect(calls).toContainEqual({
      method: 'POST',
      path: '/api/v1/notifications/read',
      body: { ids: [7] },
    })
    expect(dialog()).toBeNull()
    expect(location).toBe('/payments/4')
  })

  it('«Отметить все прочитанными» обновляет бейдж: после пометки счётчик запрашивается заново', async () => {
    items = [note({ id: 1 }), note({ id: 2, title: 'Вторая' })]
    unread = 2
    await show()
    expect(bell().textContent).toBe('2')

    await click(bell())
    unread = 0
    const all = Array.from(document.body.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Отметить все прочитанными'),
    ) as HTMLButtonElement
    await click(all)

    expect(calls).toContainEqual({ method: 'POST', path: '/api/v1/notifications/read', body: {} })
    expect(bell().getAttribute('aria-label')).toBe('Уведомления')
    expect(bell().textContent).toBe('')
  })
})

describe('SectionActions: поиск', () => {
  it('ссылка «Поиск» ведёт на /search', async () => {
    await show('/payments')
    const link = document.body.querySelector('a[aria-label="Поиск"]') as HTMLAnchorElement
    expect(link).not.toBeNull()
    expect(link.getAttribute('href')).toBe('/search')
    await click(link)
    expect(location).toBe('/search')
  })
})
