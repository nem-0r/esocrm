// @vitest-environment jsdom
/**
 * Сброс пароля в карточке сотрудника (D-38) и проверка телефона в формах сотрудников (L1).
 *
 * Страница рисуется настоящим React в jsdom, сервер подменён. Главное: кнопка
 * «Сбросить пароль» есть только там, где сервер её выполнит (не себе, не
 * принявшему приглашение, не отключённому); после подтверждения показывается
 * ссылка с честной подписью; неверный телефон не уходит на сервер.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { StaffMember } from '@/entities/types'

vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useMe: () => ({ id: 1, role: 'admin', is_admin: true }),
}))

import { StaffPage } from './StaffPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  body: unknown
}

let calls: Call[]
let resetStatus: number
let root: Root
let host: HTMLElement

function member(id: number, patch: Partial<StaffMember>): StaffMember {
  return {
    id,
    full_name: `Сотрудник ${id}`,
    avatar_color: '#7A6BE0',
    email: `s${id}@example.test`,
    phone: null,
    role: 'manager',
    is_active: true,
    online: false,
    last_seen_at: null,
    invite_pending: false,
    accounts: [],
    month_conversations: 0,
    month_sales_amount: 0,
    schedule: { enabled: false, days: [], start: null, end: null, summary: '', on_shift: false },
    ...patch,
  } as StaffMember
}

const STAFF = [
  member(1, { full_name: 'Елена Руководитель', role: 'admin' }),
  member(2, { full_name: 'Анна Менеджер', phone: '+7 916 200-10-03' }),
  member(3, { full_name: 'Дмитрий Новичок', invite_pending: true }),
  member(4, { full_name: 'Ирина Отключена', is_active: false }),
]

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  resetStatus = 200
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, path: url.pathname, body })
      if (method === 'GET' && url.pathname === '/api/v1/users') {
        return respond(200, { items: STAFF, next_cursor: null, total: STAFF.length })
      }
      if (method === 'GET' && url.pathname === '/api/v1/accounts') return respond(200, [])
      if (method === 'POST' && url.pathname === '/api/v1/users/2/reset-password') {
        if (resetStatus !== 200) {
          return respond(resetStatus, {
            error: { code: 'reset_inactive', message: 'Сотрудник отключён', details: {} },
          })
        }
        return respond(200, { link: '/reset/TOKEN-ABC', expires_at: '2026-10-13T12:00:00Z' })
      }
      if (method === 'PATCH' && url.pathname === '/api/v1/users/2') return respond(200, STAFF[1])
      return respond(200, {})
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
        createElement(MemoryRouter, { initialEntries: ['/profile/staff'] }, createElement(StaffPage)),
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

async function openMember(name: string) {
  await show()
  await click(byText('li button', name))
}

async function closeSheet() {
  await click(document.body.querySelector('[aria-label="Закрыть"]') as HTMLElement)
}

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

const resets = () => calls.filter((c) => c.path.endsWith('/reset-password'))
const writes = () => calls.filter((c) => c.method === 'PATCH' || c.method === 'POST')

describe('карточка сотрудника: сбросить пароль', () => {
  it('кнопка есть у активного сотрудника, принявшего приглашение', async () => {
    await openMember('Анна Менеджер')
    expect(byText('button', 'Сбросить пароль')).not.toBeNull()
  })

  it('нет кнопки у себя, у не принявшего приглашение и у отключённого', async () => {
    for (const name of ['Елена Руководитель', 'Дмитрий Новичок', 'Ирина Отключена']) {
      await openMember(name)
      // Карточка действительно открылась — иначе «кнопки нет» было бы пустым выводом
      expect(document.body.querySelector('[role="dialog"]')?.textContent, name).toContain(name)
      expect(byText('button', 'Сбросить пароль'), name).toBeNull()
      await closeSheet()
      await act(async () => root.unmount())
      root = createRoot(host)
    }
  })

  it('сначала подтверждение с объяснением; отмена ничего не выдаёт', async () => {
    await openMember('Анна Менеджер')
    await click(byText('button', 'Сбросить пароль'))
    expect(text()).toContain('Сбросить пароль: Анна Менеджер?')
    expect(text()).toContain('не сможет войти по старому паролю')
    expect(resets()).toHaveLength(0)
    await click(byText('button', 'Отмена'))
    expect(resets()).toHaveLength(0)
  })

  it('после подтверждения: запрос уходит, показана ссылка и честная подпись', async () => {
    await openMember('Анна Менеджер')
    await click(byText('button', 'Сбросить пароль'))
    await click(byText('button', 'Выдать ссылку'))
    expect(resets()).toEqual([{ method: 'POST', path: '/api/v1/users/2/reset-password', body: undefined }])
    expect(text()).toContain(`${window.location.origin}/reset/TOKEN-ABC`)
    expect(text()).toContain('Передайте ссылку сотруднику лично, например в Telegram')
    expect(text()).toContain('Действует 24 часа')
    expect(text()).toContain('работает один раз')
    // Скопировать можно: кнопка рабочая, а не декоративная
    expect(byText('button', 'Скопировать')).not.toBeNull()
  })

  it('отказ сервера виден руководителю, ссылки нет', async () => {
    resetStatus = 422
    await openMember('Анна Менеджер')
    await click(byText('button', 'Сбросить пароль'))
    await click(byText('button', 'Выдать ссылку'))
    expect(text()).toContain('Сотрудник отключён')
    expect(text()).not.toContain('/reset/')
  })
})

describe('телефон сотрудника в форме', () => {
  async function save() {
    await click(byText('button', 'Сохранить'))
  }

  function phoneInput(): HTMLInputElement {
    return document.body.querySelector('input[type="tel"]') as HTMLInputElement
  }

  it('поле ограничено по длине и подсказывает формат', async () => {
    await openMember('Анна Менеджер')
    expect(phoneInput().maxLength).toBe(32)
    expect(text()).toContain('Цифры, плюс только в начале, пробелы и дефисы')
  })

  it('«++--1234567»: ошибка под полем, на сервер не уходит', async () => {
    await openMember('Анна Менеджер')
    await type(phoneInput(), '++--1234567')
    await save()
    expect(text()).toContain('Проверьте формат телефона')
    expect(writes().filter((c) => c.method === 'PATCH')).toHaveLength(0)
  })

  it('телефон не менялся — в запрос не попадает (старый формат не мешает править график)', async () => {
    await openMember('Анна Менеджер')
    await save()
    const patch = calls.find((c) => c.method === 'PATCH')
    expect(patch).toBeDefined()
    expect(patch!.body).not.toHaveProperty('phone')
  })

  it('исправленный телефон уходит схлопнутым', async () => {
    await openMember('Анна Менеджер')
    await type(phoneInput(), '  +7  916 200--10-09 ')
    await save()
    const patch = calls.find((c) => c.method === 'PATCH')
    expect((patch!.body as { phone: string }).phone).toBe('+7 916 200-10-09')
  })

  it('очистка телефона уходит как null — поле действительно очищается', async () => {
    await openMember('Анна Менеджер')
    await type(phoneInput(), '')
    await save()
    const patch = calls.find((c) => c.method === 'PATCH')
    expect(patch!.body).toHaveProperty('phone', null)
  })
})
