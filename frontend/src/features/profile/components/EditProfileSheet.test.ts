// @vitest-environment jsdom
/**
 * Смена своего имени и телефона (L1, 11.10.2026): телефон проверяется на месте,
 * а 422 сервера с именем поля появляется под этим полем — раньше такая ошибка
 * не показывалась нигде (подпись не находилась, общая строка считала её «полевой»).
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { Me } from '@/entities/types'

import { EditProfileSheet } from './EditProfileSheet'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  body: unknown
}

const ME = {
  id: 2,
  full_name: 'Марина Менеджер',
  phone: '+7 916 200-10-02',
  email: 'marina@astra.ru',
} as Me

let calls: Call[]
let patchReply: { status: number; body: unknown }
let root: Root
let host: HTMLElement
let closed: boolean

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  closed = false
  patchReply = { status: 200, body: ME }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      calls.push({ method, path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return respond(patchReply.status, patchReply.body)
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

async function show() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(EditProfileSheet, {
          me: ME,
          open: true,
          onOpenChange: (open: boolean) => {
            if (!open) closed = true
          },
        }),
      ),
    )
  })
  await settle()
}

const text = () => document.body.textContent ?? ''
const phoneInput = () => document.body.querySelector('input[type="tel"]') as HTMLInputElement
const patches = () => calls.filter((c) => c.method === 'PATCH')

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function save() {
  const button = Array.from(document.body.querySelectorAll('button')).find((b) =>
    b.textContent?.includes('Сохранить'),
  )!
  await act(async () => {
    button.click()
  })
  await settle()
}

describe('профиль: имя и телефон', () => {
  it('поле телефона ограничено по длине и подсказывает формат', async () => {
    await show()
    expect(phoneInput().maxLength).toBe(32)
    expect(text()).toContain('Цифры, плюс только в начале, пробелы и дефисы')
  })

  it('«++--1234567» — ошибка под полем, запрос не уходит', async () => {
    await show()
    await type(phoneInput(), '++--1234567')
    await save()
    expect(text()).toContain('Проверьте формат телефона')
    expect(patches()).toHaveLength(0)
    expect(closed).toBe(false)
  })

  it('телефон не меняли — в запрос он не попадает', async () => {
    await show()
    await save()
    expect(patches()).toHaveLength(1)
    expect(patches()[0].body).toEqual({ full_name: 'Марина Менеджер' })
    expect(closed).toBe(true)
  })

  it('очистка телефона уходит как null', async () => {
    await show()
    await type(phoneInput(), '')
    await save()
    expect(patches()[0].body).toEqual({ full_name: 'Марина Менеджер', phone: null })
  })

  it('исправленный телефон уходит схлопнутым', async () => {
    await show()
    await type(phoneInput(), ' +7  900 000--00-01 ')
    await save()
    expect(patches()[0].body).toEqual({ full_name: 'Марина Менеджер', phone: '+7 900 000-00-01' })
  })

  it('422 сервера с полем phone показывается под полем (раньше не показывалось нигде)', async () => {
    patchReply = {
      status: 422,
      body: {
        error: {
          code: 'validation',
          message: 'Проверьте формат телефона: серверный текст',
          details: { field: 'phone', errors: [] },
        },
      },
    }
    await show()
    await type(phoneInput(), '+7 900 000-00-01') // наша проверка пропускает, сервер не согласен
    await save()
    expect(patches()).toHaveLength(1)
    expect(phoneInput().closest('label')?.textContent).toContain('серверный текст')
    expect(closed).toBe(false)
  })

  it('ошибка без поля (сеть, 500) показывается общей строкой', async () => {
    patchReply = {
      status: 500,
      body: { error: { code: 'error', message: 'Сервер недоступен', details: {} } },
    }
    await show()
    await type(phoneInput(), '+7 900 000-00-01')
    await save()
    expect(text()).toContain('Сервер недоступен')
  })
})
