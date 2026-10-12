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
// Вторая страница ленты: если задана, первая отдаёт курсор `p2`, а запрос с ним — эти строки.
let secondPage: AppNotification[] | null
// Пока не вызван, ответ на запрос второй страницы не приходит (видно «Загружаем…»).
let releaseSecondPage: (() => void) | null
let secondPageGate: Promise<void> | null
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
  secondPage = null
  releaseSecondPage = null
  secondPageGate = null
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
      if (url.pathname === '/api/v1/notifications') {
        if (secondPage && url.searchParams.get('cursor') === 'p2') {
          await secondPageGate
          return respond({ items: secondPage, next_cursor: null })
        }
        return respond({ items, next_cursor: secondPage ? 'p2' : null })
      }
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

const feedCalls = () =>
  calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/v1/notifications?'))

function events(from: number, to: number): AppNotification[] {
  const out: AppNotification[] = []
  for (let id = from; id <= to; id += 1) out.push(note({ id, title: `Событие ${id}` }))
  return out
}

describe('NotificationsSheet: «Показать ещё» и вторая страница ленты', () => {
  it('«+» в подписи говорит, что на сервере есть ещё; «Показать ещё» — только в раскрытом списке', async () => {
    items = events(1, 5)
    secondPage = events(6, 7)
    await show(true)

    expect(rowButton('Развернуть все · ещё 2+')).not.toBeNull()
    expect(rowButton('Показать ещё')).toBeNull()

    await click(rowButton('Развернуть все') as HTMLButtonElement)
    expect(document.body.textContent).toContain('Событие 5')
    expect(rowButton('Показать ещё')).not.toBeNull()
    // Раскрыть — не значит подгрузить: вторую страницу сервер ещё не отдавал.
    expect(feedCalls()).toHaveLength(1)
    expect(document.body.textContent).not.toContain('Событие 6')
  })

  it('«Показать ещё» запрашивает вторую страницу по курсору и добавляет строки в конец', async () => {
    items = events(1, 5)
    secondPage = events(6, 7)
    await show(true)
    await click(rowButton('Развернуть все') as HTMLButtonElement)
    await click(rowButton('Показать ещё') as HTMLButtonElement)

    expect(feedCalls()).toHaveLength(2)
    expect(feedCalls()[1].path).toContain('cursor=p2')
    const text = document.body.textContent ?? ''
    for (const id of [1, 5, 6, 7]) expect(text).toContain(`Событие ${id}`)
    expect(text.indexOf('Событие 5')).toBeLessThan(text.indexOf('Событие 6'))
    // Страниц больше нет: кнопка исчезла, а у списка остался только «Свернуть».
    expect(rowButton('Показать ещё')).toBeNull()
    expect(rowButton('Свернуть')).not.toBeNull()
  })

  it('пока вторая страница грузится, кнопка выключена и пишет «Загружаем…», повторный клик не шлёт запрос', async () => {
    items = events(1, 5)
    secondPage = events(6, 7)
    secondPageGate = new Promise<void>((resolve) => {
      releaseSecondPage = resolve
    })
    await show(true)
    await click(rowButton('Развернуть все') as HTMLButtonElement)
    await click(rowButton('Показать ещё') as HTMLButtonElement)

    const loading = rowButton('Загружаем…') as HTMLButtonElement
    expect(loading).not.toBeNull()
    expect(loading.disabled).toBe(true)
    await click(loading)
    expect(feedCalls()).toHaveLength(2)

    await act(async () => releaseSecondPage?.())
    await settle()
    expect(document.body.textContent).toContain('Событие 7')
    expect(rowButton('Загружаем…')).toBeNull()
  })

  it('одна страница: ни «+», ни «Показать ещё»', async () => {
    items = events(1, 5)
    await show(true)
    expect(rowButton('Развернуть все · ещё 2')?.textContent).not.toContain('+')
    await click(rowButton('Развернуть все') as HTMLButtonElement)
    expect(rowButton('Показать ещё')).toBeNull()
  })

  it('закрытие окна сворачивает список: к следующему открытию он снова на три строки', async () => {
    items = events(1, 5)
    await show(true)
    await click(rowButton('Развернуть все') as HTMLButtonElement)
    expect(document.body.textContent).toContain('Событие 5')

    // Хозяин окна в этом тесте его не закрывает — видно только то, что окно сбросило само.
    await click(document.body.querySelector('[aria-label="Закрыть"]') as HTMLElement)
    expect(closed).toBe(1)
    expect(document.body.textContent).not.toContain('Событие 5')
    expect(rowButton('Развернуть все')).not.toBeNull()
  })
})

describe('NotificationsSheet: доступность', () => {
  it('непрочитанное названо словами для диктора, прочитанное — нет', async () => {
    items = [
      note({ id: 1, title: 'Новая оплата' }),
      note({ id: 2, title: 'Старая оплата', read_at: '2026-10-10T10:00:00Z' }),
    ]
    unread = 1
    await show(true)

    const fresh = rowButton('Новая оплата') as HTMLButtonElement
    const old = rowButton('Старая оплата') as HTMLButtonElement
    expect(fresh.querySelector('.sr-only')?.textContent).toBe('Не прочитано.')
    // Слова стоят первыми — это часть названия кнопки, которое читает диктор.
    expect(fresh.textContent?.startsWith('Не прочитано.')).toBe(true)
    expect(old.querySelector('.sr-only')).toBeNull()
    expect(old.textContent).not.toContain('Не прочитано')
  })

  it('и у строки без ссылки (обычный текст) непрочитанное названо словами', async () => {
    items = [
      note({ id: 3, entity_type: null, entity_id: null, title: 'Без ссылки' }),
      note({
        id: 4,
        entity_type: null,
        entity_id: null,
        title: 'Без ссылки, прочитано',
        read_at: '2026-10-10T10:00:00Z',
      }),
    ]
    await show(true)
    const rows = Array.from(document.body.querySelectorAll('li'))
    expect(rows).toHaveLength(2)
    expect(rows[0].querySelector('.sr-only')?.textContent).toBe('Не прочитано.')
    expect(rows[1].querySelector('.sr-only')).toBeNull()
  })

  it('окно — диалог с названием «Уведомления», кнопка закрытия подписана', async () => {
    items = [note({ id: 5 })]
    await show(true)
    const dialog = document.body.querySelector('[role="dialog"]')
    expect(dialog).not.toBeNull()
    const labelledBy = dialog?.getAttribute('aria-labelledby')
    expect(document.getElementById(labelledBy ?? '')?.textContent).toBe('Уведомления')
    expect(dialog?.querySelector('[aria-label="Закрыть"]')).not.toBeNull()
  })
})
