// @vitest-environment jsdom
/**
 * Форма «Данные клиента» (L1, 11.10.2026): границы даты рождения, длина полей и
 * ошибки у своего поля — как их видит человек.
 *
 * Страница рисуется настоящим React в jsdom, сервер подменён. Правила сами по
 * себе проверены в `clientForm.test.ts` и `validation.test.ts`; здесь — что они
 * подключены к форме и что ответ сервера (422 с именем поля) появляется под полем.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useMe: () => ({ id: 1, role: 'admin', is_admin: true }),
  useAuth: () => ({ me: { id: 1, role: 'admin', is_admin: true }, isAdmin: true }),
}))

import { todayIso } from '@/shared/lib/validation'

import { ClientCardPage } from './ClientCardPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  body: unknown
}

const CARD = {
  id: 5,
  name: 'Анна Петрова',
  phone: '+7 916 200-10-01',
  tg_username: null,
  birth_date: '1990-05-17',
  birth_time: '14:30:00',
  birth_time_approx: null,
  birth_city: 'Москва',
  zodiac_sign: 'Телец',
  data_complete: true,
  first_contact_at: '2026-01-01T10:00:00Z',
  created_at: '2026-01-01T10:00:00Z',
  last_contact_at: null,
  paid_amount: 0,
  paid_count: 0,
  awaiting_amount: 0,
  conversations_count: 0,
  source_code: null,
  source: null,
  tg_first_name: null,
  tg_last_name: null,
  display_name: 'Анна Петрова',
  created_via_account: null,
  accounts_count: 0,
  pdn_consent_at: null,
  pdn_consent_version: null,
  marketing_consent: true,
  marketing_consent_at: null,
  conversations: [],
}

let calls: Call[]
let patchReply: { status: number; body: unknown }
let root: Root
let host: HTMLElement

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  patchReply = { status: 200, body: CARD }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, path: url.pathname, body })
      if (method === 'GET' && url.pathname === '/api/v1/clients/5') return respond(200, CARD)
      if (method === 'PATCH' && url.pathname === '/api/v1/clients/5') {
        return respond(patchReply.status, patchReply.body)
      }
      if (method === 'GET' && url.pathname === '/api/v1/clients') {
        return respond(200, { items: [], next_cursor: null, total: 0 })
      }
      return respond(200, [])
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
  for (let i = 0; i < 8; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

async function show() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(
          MemoryRouter,
          { initialEntries: ['/clients/5'] },
          createElement(
            Routes,
            null,
            createElement(Route, { path: '/clients/:clientId', element: createElement(ClientCardPage) }),
          ),
        ),
      ),
    )
  })
  await settle()
}

const text = () => document.body.textContent ?? ''

function byText(selector: string, needle: string): HTMLElement | null {
  const nodes = Array.from(document.body.querySelectorAll<HTMLElement>(selector))
  return nodes.find((node) => node.textContent?.includes(needle)) ?? null
}

async function click(el: HTMLElement | null) {
  expect(el).not.toBeNull()
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
  await settle()
}

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function openEdit() {
  await show()
  await click(byText('button', 'Изменить'))
}

function dialogInput(selector: string): HTMLInputElement {
  return document.body.querySelector(`[role="dialog"] ${selector}`) as HTMLInputElement
}

const patches = () => calls.filter((c) => c.method === 'PATCH')

describe('форма «Данные клиента»', () => {
  it('поля ограничены: дата — 1900…сегодня, телефон — 32 символа, имя и город — 120', async () => {
    await openEdit()
    const date = dialogInput('input[type="date"]')
    expect(date.min).toBe('1900-01-01')
    expect(date.max).toBe(todayIso())
    expect(dialogInput('input[type="tel"]').maxLength).toBe(32)
    const texts = Array.from(
      document.body.querySelectorAll<HTMLInputElement>('[role="dialog"] input:not([type])'),
    )
    expect(texts.map((i) => i.maxLength)).toEqual([120, 120]) // имя и город
  })

  it('дата 2999 отклоняется на месте, запрос не уходит', async () => {
    await openEdit()
    await type(dialogInput('input[type="date"]'), '2999-01-01')
    await click(byText('button', 'Сохранить'))
    expect(text()).toContain('Дата рождения не может быть в будущем')
    expect(patches()).toHaveLength(0)
  })

  it('телефон «++--1234567» отклоняется на месте', async () => {
    await openEdit()
    await type(dialogInput('input[type="tel"]'), '++--1234567')
    await click(byText('button', 'Сохранить'))
    expect(text()).toContain('Проверьте формат телефона')
    expect(patches()).toHaveLength(0)
  })

  it('ошибка у поля исчезает, как только человек начинает его править', async () => {
    await openEdit()
    await type(dialogInput('input[type="tel"]'), 'abc')
    await click(byText('button', 'Сохранить'))
    expect(text()).toContain('Проверьте формат телефона')
    await type(dialogInput('input[type="tel"]'), '+7 916 200-10-02')
    expect(text()).not.toContain('Проверьте формат телефона: цифры')
  })

  it('верные данные уходят одним запросом, нетронутые поля не отправляются', async () => {
    await openEdit()
    await type(dialogInput('input[type="tel"]'), '  +7 916   200-10-02 ')
    await click(byText('button', 'Сохранить'))
    expect(patches()).toHaveLength(1)
    expect(patches()[0].body).toEqual({
      birth_time: '14:30',
      birth_time_approx: null,
      phone: '+7 916 200-10-02',
    })
  })

  it('422 сервера с именем поля показывается под этим полем, а не общей строкой', async () => {
    patchReply = {
      status: 422,
      body: {
        error: {
          code: 'validation',
          message: 'Дата рождения не может быть в будущем',
          details: { field: 'birth_date', errors: [] },
        },
      },
    }
    await openEdit()
    await type(dialogInput('input[type="date"]'), '2000-01-01') // сами пропускаем, сервер не согласен
    await click(byText('button', 'Сохранить'))
    expect(patches()).toHaveLength(1)
    const field = dialogInput('input[type="date"]').closest('label')
    expect(field?.textContent).toContain('Дата рождения не может быть в будущем')
  })

  it('ошибка без поля — общей строкой внизу формы', async () => {
    patchReply = {
      status: 500,
      body: { error: { code: 'error', message: 'Сервер недоступен', details: {} } },
    }
    await openEdit()
    await type(dialogInput('input[type="tel"]'), '+7 916 200-10-02')
    await click(byText('button', 'Сохранить'))
    expect(document.body.querySelector('[role="dialog"]')?.textContent).toContain('Сервер недоступен')
  })

  it('при повторном открытии в полях свежие значения, а не прошлый ввод', async () => {
    await openEdit()
    await type(dialogInput('input[type="tel"]'), 'abc')
    await click(document.body.querySelector('[aria-label="Закрыть"]') as HTMLElement)
    await click(byText('button', 'Изменить'))
    expect(dialogInput('input[type="tel"]').value).toBe('+7 916 200-10-01')
  })
})
