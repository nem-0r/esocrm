// @vitest-environment jsdom
/**
 * Экраны «Профиль» и «Аккаунты» целиком (замечания BUG-09, L6): настоящий React в
 * jsdom, сервер подменён. Проверяем то, что видит человек, а не внутренности:
 * чем показана плашка (ссылка или текст), включается ли фильтр по адресу,
 * что написано под переключателем «Приём заявок».
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Account, Me } from '@/entities/types'

const state = vi.hoisted(() => ({ me: null as unknown as Me, isAdmin: false }))
vi.mock('@/shared/hooks/useAuth', () => ({
  useAuth: () => ({ me: state.me, isAdmin: state.isAdmin, logout: async () => undefined }),
  useMe: () => state.me,
}))

import { AccountsPage } from './AccountsPage'
import { ProfilePage } from './ProfilePage'

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function makeMe(patch: Partial<Me>): Me {
  return {
    id: 7,
    full_name: 'Марина',
    email: 'marina@astra.ru',
    phone: null,
    role: 'manager',
    is_active: true,
    accepting_leads: true,
    avatar_color: '#888888',
    last_seen_at: null,
    is_admin: false,
    sees_all_accounts: false,
    account_ids: [],
    works_since: '2026-01-01T00:00:00Z',
    month_conversations: 0,
    month_sales_amount: 0,
    avg_response_seconds: null,
    accounts_attention: 0,
    schedule: {
      enabled: false,
      days: [],
      start: null,
      end: null,
      summary: '',
      on_shift: false,
    },
    demo_mode: false,
    robokassa_enabled: false,
    ...patch,
  }
}

function makeAccount(id: number, patch: Partial<Account>): Account {
  return {
    id,
    title: `Аккаунт ${id}`,
    funnel_stage: 'sales',
    phone: '+79990000000',
    status: 'connected',
    status_reason: null,
    tg_username: null,
    last_activity_at: null,
    is_active: true,
    needs_attention: false,
    managers: [{ id: 7, full_name: 'Марина', online: false }],
    conversations_count: 0,
    ...patch,
  }
}

let accounts: Account[]
let summary: { total: number; connected: number; attention: number; conversations_total: number }
let root: Root
let host: HTMLElement
let search = ''

function Probe(): ReactElement {
  search = useLocation().search
  return createElement('span')
}

function respond(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  accounts = []
  summary = { total: 0, connected: 0, attention: 0, conversations_total: 0 }
  search = ''
  state.isAdmin = false
  state.me = makeMe({})
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string) => {
      const path = new URL(input, 'http://localhost').pathname
      switch (path) {
        case '/api/v1/accounts':
          return respond(accounts)
        case '/api/v1/accounts/summary':
          return respond(summary)
        case '/api/v1/notifications/unread-count':
          return respond({ count: 0 })
        case '/api/v1/settings':
          return respond({
            timezone: 'Europe/Moscow',
            working_hours_enabled: false,
            working_hours_start: '10:00:00',
            working_hours_end: '19:00:00',
            working_days: [1, 2, 3, 4, 5],
          })
        case '/api/v1/users':
          return respond({ items: [], next_cursor: null })
        default:
          return respond({})
      }
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

async function show(page: ReactElement, url: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MemoryRouter, { initialEntries: [url] }, createElement(Probe), page),
      ),
    )
  })
  await settle()
}

function findText(text: string): HTMLElement | null {
  const all = Array.from(host.querySelectorAll<HTMLElement>('*'))
  return all.filter((el) => el.textContent?.trim() === text).pop() ?? null
}

describe('BUG-09. Плашка «аккаунт требует внимания»', () => {
  it('менеджер: текст без ссылки, и сказано один раз', async () => {
    accounts = [
      makeAccount(1, { status: 'error', title: 'Изабель' }),
      makeAccount(2, { status: 'connected' }),
    ]
    state.me = makeMe({ accounts_attention: 1 })
    await show(createElement(ProfilePage), '/profile')

    const banner = findText('Аккаунт отключён, сообщите руководителю')
    expect(banner).not.toBeNull()
    expect(banner?.closest('a')).toBeNull()
    expect(host.querySelector('a[href*="attention"]')).toBeNull()
    // Прежний текст «Нет связи с аккаунтом…» в блоке «Мои аккаунты» не дублирует плашку.
    expect(host.textContent).not.toContain('Нет связи с аккаунтом')
    expect(host.textContent).not.toContain('Обратитесь к руководителю')
  })

  it('менеджер: все аккаунты подключены — плашки нет', async () => {
    accounts = [makeAccount(1, { status: 'connected' })]
    state.me = makeMe({ accounts_attention: 0 })
    await show(createElement(ProfilePage), '/profile')
    expect(host.textContent).not.toContain('сообщите руководителю')
  })

  it('руководитель: плашка — ссылка в «Аккаунты» с включённым фильтром', async () => {
    state.isAdmin = true
    state.me = makeMe({ role: 'admin', is_admin: true, sees_all_accounts: true })
    summary = { total: 3, connected: 1, attention: 2, conversations_total: 0 }
    await show(createElement(ProfilePage), '/profile')

    const link = host.querySelector('a[href="/profile/accounts?attention=1"]')
    expect(link).not.toBeNull()
    expect(link?.textContent).toContain('2 аккаунта требуют внимания')
  })

  it('руководитель: ?attention=1 включает фильтр, его можно снять', async () => {
    state.isAdmin = true
    state.me = makeMe({ role: 'admin', is_admin: true, sees_all_accounts: true })
    accounts = [
      makeAccount(1, { title: 'Рабочий', status: 'connected' }),
      makeAccount(2, { title: 'Сломанный', status: 'error', needs_attention: true }),
    ]
    await show(createElement(AccountsPage), '/profile/accounts?attention=1')

    expect(host.textContent).toContain('Сломанный')
    expect(host.textContent).not.toContain('Рабочий')
    const toggle = host.querySelector<HTMLElement>('button[role="switch"]')
    expect(toggle?.getAttribute('aria-checked')).toBe('true')

    await act(async () => {
      toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await settle()
    expect(host.textContent).toContain('Рабочий')
    expect(host.textContent).toContain('Сломанный')
    expect(search).toBe('')
  })

  it('руководитель: без параметра фильтр выключен и включается переключателем', async () => {
    state.isAdmin = true
    state.me = makeMe({ role: 'admin', is_admin: true, sees_all_accounts: true })
    accounts = [
      makeAccount(1, { title: 'Рабочий', status: 'connected' }),
      makeAccount(2, { title: 'Сломанный', status: 'error', needs_attention: true }),
    ]
    await show(createElement(AccountsPage), '/profile/accounts')
    expect(host.textContent).toContain('Рабочий')

    const toggle = host.querySelector<HTMLElement>('button[role="switch"]')
    await act(async () => {
      toggle?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })
    await settle()
    expect(host.textContent).not.toContain('Рабочий')
    expect(search).toBe('?attention=1')
  })
})

describe('L6. Подпись под переключателем «Приём заявок»', () => {
  async function hintFor(leads: boolean, list: Account[]): Promise<string> {
    accounts = list
    state.me = makeMe({ accepting_leads: leads })
    await show(createElement(ProfilePage), '/profile')
    const label = findText(leads ? 'Приём заявок включён' : 'Приём заявок выключен')
    return label?.parentElement?.textContent ?? ''
  }

  it('включён и менеджер на аккаунте один: подпись про закрепление', async () => {
    const text = await hintFor(true, [makeAccount(1, {})])
    expect(text).toContain('закрепляются за вами сразу')
    expect(text).not.toContain('не назначаются')
  })

  it('выключен: подпись другая и не обещает «не назначаются»', async () => {
    const text = await hintFor(false, [makeAccount(1, {})])
    expect(text).toContain('Приём заявок выключен')
    expect(text).toContain('за вами не закрепляются')
    expect(text).not.toContain('Пока выключен, новые диалоги вам не назначаются')
  })

  it('на аккаунте несколько менеджеров: честно «ничего не меняет»', async () => {
    const shared = makeAccount(1, {
      managers: [
        { id: 7, full_name: 'Марина', online: false },
        { id: 8, full_name: 'Олег', online: false },
      ],
    })
    expect(await hintFor(true, [shared])).toContain('ничего не меняет')
  })
})
