// @vitest-environment jsdom
/**
 * Страница поиска целиком, как её видит человек (D-35, L4).
 *
 * Библиотеки тестирования компонентов в проекте нет, поэтому страница рисуется
 * настоящим React в jsdom, а сервер подменён: проверяем, какие запросы уходят.
 * Главное — история поиска пишется только по Enter или клику по результату, а
 * набор по буквам, смена вкладки и клик по пункту самой истории её не трогают.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SearchResults } from '@/entities/types'

vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useMe: () => ({ is_admin: true }),
}))

import { SearchPage } from './SearchPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

let calls: Call[]
let results: SearchResults
let history: { id: number; query: string; created_at: string }[]
let root: Root
let host: HTMLElement

function emptyResults(patch: Partial<SearchResults> = {}): SearchResults {
  return { clients: [], deals: [], chats: [], files: [], managers: [], ...patch }
}

function client(id: number, patch: Partial<SearchResults['clients'][number]> = {}) {
  return {
    id,
    name: `Клиент ${id}`,
    tg_username: null,
    phone: null,
    paid_amount: 0,
    ...patch,
  }
}

function respond(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  results = emptyResults()
  history = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, path: url.pathname, query: url.searchParams, body })
      if (url.pathname === '/api/v1/search/history') {
        if (method === 'POST') {
          const q = String((body as { q: string }).q)
          history = [{ id: Date.now(), query: q, created_at: '2026-10-12T10:00:00Z' }, ...history]
        }
        return respond(history)
      }
      if (url.pathname === '/api/v1/search') return respond(results)
      if (url.pathname === '/api/v1/conversations') return respond({ items: [], next_cursor: null })
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

async function wait(ms = 0) {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, ms))
  })
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await wait()
}

async function show() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(MemoryRouter, { initialEntries: ['/search'] }, createElement(SearchPage)),
      ),
    )
  })
  await settle()
}

function input(): HTMLInputElement {
  return document.body.querySelector('input') as HTMLInputElement
}

async function type(value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input(), value)
    input().dispatchEvent(new Event('input', { bubbles: true }))
  })
  // Дебаунс строки поиска — 300 мс.
  await wait(400)
  await settle()
}

async function pressEnter() {
  await act(async () => {
    input().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  })
  await settle()
}

function byText(selector: string, text: string): HTMLElement | null {
  const nodes = Array.from(document.body.querySelectorAll<HTMLElement>(selector))
  return nodes.find((node) => node.textContent?.includes(text)) ?? null
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
  await settle()
}

const searches = () => calls.filter((c) => c.method === 'GET' && c.path === '/api/v1/search')
const writes = () => calls.filter((c) => c.method === 'POST' && c.path === '/api/v1/search/history')

describe('SearchPage: история пишется только по явному действию', () => {
  it('набор по буквам ищет, но историю не пишет', async () => {
    results = emptyResults({ clients: [client(1)] })
    await show()
    await type('Ксения')
    expect(searches().length).toBeGreaterThan(0)
    expect(writes()).toHaveLength(0)
  })

  it('Enter запоминает запрос ровно один раз', async () => {
    results = emptyResults({ clients: [client(1)] })
    await show()
    await type('  Ксения Сергеева ')
    await pressEnter()
    expect(writes()).toHaveLength(1)
    expect(writes()[0].body).toEqual({ q: 'Ксения Сергеева' })
  })

  it('Enter на слишком коротком запросе ничего не пишет', async () => {
    await show()
    await type('к')
    await pressEnter()
    expect(writes()).toHaveLength(0)
  })

  it('смена вкладки-фильтра ищет заново, но историю не пишет', async () => {
    results = emptyResults({ clients: [client(1)] })
    await show()
    await type('Ксения')
    const before = searches().length
    await click(byText('button', 'Оплаты') as HTMLElement)
    expect(searches().length).toBeGreaterThan(before)
    expect(writes()).toHaveLength(0)
  })

  it('клик по результату запоминает запрос', async () => {
    results = emptyResults({ clients: [client(5, { name: 'Ксения Сергеева' })] })
    await show()
    await type('Сергеева')
    await click(byText('a', 'Ксения Сергеева') as HTMLElement)
    expect(writes()).toHaveLength(1)
    expect(writes()[0].body).toEqual({ q: 'Сергеева' })
  })

  it('клик по пункту истории подставляет запрос, ищет и не создаёт дубль', async () => {
    history = [{ id: 1, query: 'Ксения Сергеева', created_at: '2026-10-12T10:00:00Z' }]
    results = emptyResults({ clients: [client(5, { name: 'Ксения Сергеева' })] })
    await show()
    await click(byText('button', 'Ксения Сергеева') as HTMLElement)
    expect(input().value).toBe('Ксения Сергеева')
    expect(searches().some((c) => c.query.get('q') === 'Ксения Сергеева')).toBe(true)
    expect(writes()).toHaveLength(0)
  })

  it('после записи история на экране обновляется ответом сервера', async () => {
    results = emptyResults({ clients: [client(1)] })
    await show()
    await type('Ксения')
    await pressEnter()
    // Вернулись к пустой строке: пункт «Ксения» уже в списке недавних запросов.
    await type('')
    expect(byText('button', 'Ксения')).not.toBeNull()
  })
})

describe('SearchPage: что написано под именем клиента', () => {
  it('имя, затем @username · телефон', async () => {
    results = emptyResults({
      clients: [
        client(1, { name: 'Ксения Сергеева', tg_username: 'ksenia', phone: '+7 958 142-61-76' }),
      ],
    })
    await show()
    await type('Ксения')
    expect(document.body.textContent).toContain('Ксения Сергеева')
    expect(document.body.textContent).toContain('@ksenia · +7 958 142-61-76')
    expect(document.body.textContent).not.toContain('id 1 ·')
  })

  it('«id N» — только если больше показывать нечего', async () => {
    results = emptyResults({ clients: [client(703, { name: '123456789' })] })
    await show()
    await type('1234')
    expect(document.body.textContent).toContain('id 703')
  })
})

describe('SearchPage: «Показать все»', () => {
  it('кнопка с числом, клик переключает на вкладку группы и просит до 50', async () => {
    results = emptyResults({
      clients: [client(1), client(2), client(3), client(4), client(5)],
      totals: { clients: 17 },
    })
    await show()
    await type('Анна')
    expect(searches().at(-1)?.query.get('limit')).toBe('5')
    expect(document.body.textContent).toContain('Клиенты · 17')

    const button = byText('button', 'Показать все (17)')
    expect(button).not.toBeNull()
    await click(button as HTMLElement)

    const last = searches().at(-1)
    expect(last?.query.get('types')).toBe('clients')
    expect(last?.query.get('limit')).toBe('50')
    expect(byText('button', 'Показать все')).toBeNull()
    expect(writes()).toHaveLength(0)
  })

  it('кнопки нет, когда показано всё', async () => {
    results = emptyResults({ clients: [client(1), client(2)], totals: { clients: 2 } })
    await show()
    await type('Анна')
    expect(byText('button', 'Показать все')).toBeNull()
  })

  it('больше 50: подпись «50+» и честное предупреждение в полном списке', async () => {
    results = emptyResults({
      clients: Array.from({ length: 5 }, (_, i) => client(i + 1)),
      totals: { clients: 51 },
    })
    await show()
    await type('Анна')
    expect(byText('button', 'Показать все (50+)')).not.toBeNull()

    results = emptyResults({
      clients: Array.from({ length: 50 }, (_, i) => client(i + 1)),
      totals: { clients: 51 },
    })
    await click(byText('button', 'Показать все (50+)') as HTMLElement)
    expect(document.body.textContent).toContain('Показаны первые 50')
  })
})

describe('SearchPage: пустой результат', () => {
  it('подсказывает про написание и про то, что имя ищется как в Telegram', async () => {
    await show()
    await type('Неттакого')
    expect(document.body.textContent).toContain('ничего не найдено')
    expect(document.body.textContent).toContain('Проверьте написание')
    expect(document.body.textContent).toContain('Ksenia')
  })
})
