// @vitest-environment jsdom
/**
 * Период в разделе «Оплаты», как его видит человек (BUG-06, D-39, L2).
 *
 * Раздел рисуется настоящим React в jsdom, сервер подменён: проверяем, какие запросы
 * уходят и что написано на экране. «Сегодня» — по часовому поясу организации из настроек,
 * поэтому часы заморожены на 22:30 UTC: в Москве это уже следующий день.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useMe: () => ({ is_admin: true }),
}))
// Колокольчик и профиль к периоду отношения не имеют и тянут свои запросы.
vi.mock('@/app/layout/SectionActions', () => ({ SectionActions: () => null }))

import { PaymentsLayout } from './PaymentsLayout'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  query: URLSearchParams
}

let calls: Call[]
let root: Root
let host: HTMLElement

function respond(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  // 22:30 UTC 7 октября = 01:30 8 октября в Москве. Таймеры не трогаем — только Date.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-07T22:30:00Z'))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      calls.push({ method: init?.method ?? 'GET', path: url.pathname, query: url.searchParams })
      if (url.pathname === '/api/v1/settings') return respond({ timezone: 'Europe/Moscow' })
      if (url.pathname === '/api/v1/deals') return respond({ items: [], next_cursor: null })
      if (url.pathname === '/api/v1/deals/summary') {
        return respond({
          paid_amount: 0,
          paid_count: 0,
          awaiting_amount: 0,
          awaiting_count: 0,
          clients_with_deals: 0,
        })
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
  vi.useRealTimers()
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
        createElement(
          MemoryRouter,
          { initialEntries: ['/payments'] },
          createElement(PaymentsLayout, { selectedId: null }),
        ),
      ),
    )
  })
  await settle()
}

const text = () => document.body.textContent ?? ''

function byText(selector: string, label: string): HTMLElement {
  const nodes = Array.from(document.body.querySelectorAll<HTMLElement>(selector))
  const found = nodes.find((node) => node.textContent?.trim() === label)
  if (!found) throw new Error(`Нет элемента «${label}»`)
  return found
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
  await settle()
}

function dateField(label: string): HTMLInputElement {
  const caption = Array.from(document.body.querySelectorAll('label')).find((node) =>
    node.textContent?.startsWith(label),
  )
  return caption?.querySelector('input') as HTMLInputElement
}

async function setDate(label: string, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  const field = dateField(label)
  await act(async () => {
    setter.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await settle()
}

const lists = () => calls.filter((c) => c.method === 'GET' && c.path === '/api/v1/deals')
const exportButton = () => byText('button', 'Выгрузить') as HTMLButtonElement

describe('Оплаты: пресеты периода и подпись с датами', () => {
  it('по умолчанию год, подпись показывает даты по поясу организации', async () => {
    await show()
    // 8 октября, а не 7-е: в Москве уже следующее утро.
    expect(text()).toContain('9 октября 2025 — 8 октября 2026')
    const last = lists().at(-1)
    expect(last?.query.get('date_from')).toBe('2025-10-09')
    expect(last?.query.get('date_to')).toBe('2026-10-08')
  })

  it('«Месяц» — календарный: с 1-го числа по сегодня, и запрос идёт с этими датами', async () => {
    await show()
    await click(byText('button', 'Месяц'))
    expect(text()).toContain('1 октября — 8 октября 2026')
    const last = lists().at(-1)
    expect(last?.query.get('date_from')).toBe('2026-10-01')
    expect(last?.query.get('date_to')).toBe('2026-10-08')
  })

  it('неделя и квартал считаются как раньше', async () => {
    await show()
    await click(byText('button', 'Неделя'))
    expect(text()).toContain('2 октября — 8 октября 2026')
    await click(byText('button', 'Квартал'))
    expect(text()).toContain('11 июля — 8 октября 2026')
  })
})

describe('Оплаты: неверный период не уходит на сервер (L2)', () => {
  async function openCustom() {
    await show()
    await click(byText('button', 'Свой'))
  }

  it('«с» позже «по»: красная подсказка, вместо списка — сообщение, «Выгрузить» выключена', async () => {
    await openCustom()
    await setDate('С даты', '2026-10-09')
    const sent = lists().length

    // Причина названа один раз — в подсказке под полями (она же role="alert"); вместо списка
    // стоит итог без повтора причины, иначе диктор читал бы одно и то же дважды.
    expect((text().match(/Начало периода позже конца/g) ?? []).length).toBe(1)
    expect(
      Array.from(document.body.querySelectorAll('[role="alert"]')).map((node) => node.textContent),
    ).toEqual(['Начало периода позже конца'])
    expect(text()).toContain('Оплаты не показаны — период указан неверно')
    expect(text()).not.toContain('Оплат за период нет')
    expect(exportButton().disabled).toBe(true)

    await settle()
    expect(lists()).toHaveLength(sent)
    expect(lists().some((c) => c.query.get('date_from') === '2026-10-09')).toBe(false)
    // Плитки тоже не запрашиваются с неверными датами.
    expect(
      calls.some(
        (c) => c.path === '/api/v1/deals/summary' && c.query.get('date_from') === '2026-10-09',
      ),
    ).toBe(false)
  })

  it('пустая дата не подменяется молча периодом по умолчанию', async () => {
    await openCustom()
    const sent = lists().length
    await setDate('По дату', '')
    expect((text().match(/Укажите обе даты периода/g) ?? []).length).toBe(1)
    expect(exportButton().disabled).toBe(true)
    expect(lists()).toHaveLength(sent)
  })

  it('год вне 1900–2100 — тоже ошибка', async () => {
    await openCustom()
    await setDate('С даты', '0002-10-05')
    expect((text().match(/Период должен лежать между 1900 и 2100 годами/g) ?? []).length).toBe(1)
    expect(exportButton().disabled).toBe(true)
    // Поля помечены неверными для вспомогательных технологий.
    expect(dateField('С даты').getAttribute('aria-invalid')).toBe('true')
    expect(dateField('По дату').getAttribute('aria-invalid')).toBe('true')
  })

  it('исправили даты — запросы возобновляются, кнопка снова работает, подпись вернулась', async () => {
    await openCustom()
    await setDate('С даты', '2026-10-09')
    expect(exportButton().disabled).toBe(true)

    await setDate('С даты', '2026-10-05')
    expect(text()).not.toContain('Начало периода позже конца')
    expect(text()).toContain('5 октября — 8 октября 2026')
    expect(exportButton().disabled).toBe(false)
    const last = lists().at(-1)
    expect(last?.query.get('date_from')).toBe('2026-10-05')
    expect(last?.query.get('date_to')).toBe('2026-10-08')
  })
})
