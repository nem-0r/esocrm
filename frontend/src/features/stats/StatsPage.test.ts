// @vitest-environment jsdom
/**
 * Период «Свой» в разделе «Статистика», как его видит человек (D-39, L2).
 *
 * Страница рисуется настоящим React в jsdom, сервер подменён: проверяем, что написано
 * в окне «Период», можно ли нажать «Применить» и какие запросы уходят на сервер.
 * Плитки и таблицы к периоду отношения не имеют — они заменены пустышками.
 * «Сегодня» — по часовому поясу организации: часы заморожены на 22:30 UTC, в Москве это
 * уже 8 октября.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/hooks/useAuth', () => ({ useAuth: () => ({ isAdmin: true }) }))
// Колокольчик с поиском тянут свои запросы — к периоду они не относятся.
vi.mock('@/app/layout/SectionActions', () => ({ SectionActions: () => null }))
vi.mock('@/features/stats/components/OverviewSections', () => ({
  SalesTiles: () => null,
  ClientTiles: () => null,
  SpeedTiles: () => null,
}))
vi.mock('@/features/stats/components/BreakdownTables', () => ({
  ServicesTable: () => null,
  AccountsTable: () => null,
  ManagersTable: () => null,
}))

import { StatsPage } from './StatsPage'

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
  // Таймеры не трогаем — только Date.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-07T22:30:00Z'))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      calls.push({ method: init?.method ?? 'GET', path: url.pathname, query: url.searchParams })
      if (url.pathname === '/api/v1/settings') return respond({ timezone: 'Europe/Moscow' })
      // Продаж нет — график не рисуется (в jsdom ему не на чем считать размеры).
      if (url.pathname === '/api/v1/stats/series') return respond({ points: [] })
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
        createElement(MemoryRouter, { initialEntries: ['/stats'] }, createElement(StatsPage)),
      ),
    )
  })
  await settle()
}

const text = () => document.body.textContent ?? ''
const dialog = () => document.body.querySelector('[role="dialog"]')

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
  const caption = Array.from(document.body.querySelectorAll('label')).find(
    (node) => node.textContent?.trim() === label,
  )
  if (!caption) throw new Error(`Нет поля «${label}»`)
  return caption.querySelector('input') as HTMLInputElement
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

async function openCustom() {
  await show()
  await click(byText('button', 'Свой'))
}

const overviews = () => calls.filter((c) => c.path === '/api/v1/stats/overview')
const series = () => calls.filter((c) => c.path === '/api/v1/stats/series')
const applyButton = () => byText('button', 'Применить') as HTMLButtonElement
const alerts = () =>
  Array.from(document.body.querySelectorAll('[role="alert"]')).map((node) => node.textContent)

describe('Статистика: период «Свой»', () => {
  it('по умолчанию календарный месяц, «Свой» открывает окно с этими датами', async () => {
    await openCustom()
    expect(text()).toContain('1 октября — 8 октября 2026')
    expect(dialog()).not.toBeNull()
    expect(dateField('С').value).toBe('2026-10-01')
    expect(dateField('По').value).toBe('2026-10-08')
    expect(applyButton().disabled).toBe(false)
    expect(alerts()).toEqual([])
  })

  it('поля дат ограничены 1900–2100 — как в «Оплатах»', async () => {
    await openCustom()
    expect(dateField('С').min).toBe('1900-01-01')
    expect(dateField('С').max).toBe('2026-10-08')
    expect(dateField('По').min).toBe('2026-10-01')
    expect(dateField('По').max).toBe('2100-12-31')
  })

  it('«с» позже «по»: подсказка, «Применить» выключена, запросы не уходят', async () => {
    await openCustom()
    const sent = overviews().length
    await setDate('С', '2026-10-09')

    expect(alerts()).toEqual(['Начало периода позже конца'])
    expect(applyButton().disabled).toBe(true)
    expect(dateField('С').getAttribute('aria-invalid')).toBe('true')
    expect(dateField('По').getAttribute('aria-invalid')).toBe('true')

    // Нажатие на выключенную кнопку ничего не меняет: период прежний, окно открыто.
    await click(applyButton())
    expect(dialog()).not.toBeNull()
    expect(text()).toContain('1 октября — 8 октября 2026')
    expect(overviews()).toHaveLength(sent)
  })

  it.each([
    ['С', '0002-10-05'],
    ['По', '3000-01-01'],
  ])('год вне 1900–2100 («%s» = %s): подсказка, «Применить» выключена, на сервер не уходит', async (label, value) => {
    await openCustom()
    const sent = overviews().length
    await setDate(label, value)

    expect(alerts()).toEqual(['Период должен лежать между 1900 и 2100 годами'])
    expect(applyButton().disabled).toBe(true)

    await click(applyButton())
    expect(overviews()).toHaveLength(sent)
    expect(overviews().some((c) => c.query.get('date_from') === '0002-10-05')).toBe(false)
    expect(overviews().some((c) => c.query.get('date_to') === '3000-01-01')).toBe(false)
  })

  it('пустая дата: «Укажите обе даты периода», «Применить» выключена', async () => {
    await openCustom()
    await setDate('По', '')
    expect(alerts()).toEqual(['Укажите обе даты периода'])
    expect(applyButton().disabled).toBe(true)
  })

  it('исправили даты — подсказка пропала, «Применить» работает, запрос уходит с этими датами', async () => {
    await openCustom()
    await setDate('С', '0002-10-05')
    expect(applyButton().disabled).toBe(true)

    await setDate('С', '2026-09-20')
    await setDate('По', '2026-09-30')
    expect(alerts()).toEqual([])
    expect(applyButton().disabled).toBe(false)
    expect(text()).toContain('Выбрано 11 дней.')

    await click(applyButton())
    expect(dialog()).toBeNull()
    expect(text()).toContain('20 сентября — 30 сентября 2026')
    const last = overviews().at(-1)
    expect(last?.query.get('date_from')).toBe('2026-09-20')
    expect(last?.query.get('date_to')).toBe('2026-09-30')
    expect(series().at(-1)?.query.get('granularity')).toBe('day')
  })

  it('длинный период: дни недоступны, график запрашивается по неделям', async () => {
    await openCustom()
    await setDate('С', '2026-01-01')
    expect(text()).toContain('При периоде больше двух месяцев дни недоступны.')
    // «Дни» есть и на странице, и в окне — берём ту, что в окне.
    const days = Array.from(dialog()!.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.trim() === 'Дни',
    )
    expect(days?.disabled).toBe(true)

    await click(applyButton())
    const last = series().at(-1)
    expect(last?.query.get('date_from')).toBe('2026-01-01')
    expect(last?.query.get('granularity')).toBe('week')
  })

  it('закрыли окно без «Применить» — прежний период остаётся, черновик не просачивается', async () => {
    await openCustom()
    await setDate('С', '2026-09-01')
    const sent = overviews().length

    await click(document.body.querySelector('[aria-label="Закрыть"]') as HTMLElement)
    expect(dialog()).toBeNull()
    expect(text()).toContain('1 октября — 8 октября 2026')
    expect(overviews()).toHaveLength(sent)

    // Снова «Свой» — окно начинается с боевых дат, а не с брошенного черновика.
    await click(byText('button', 'Свой'))
    expect(dateField('С').value).toBe('2026-10-01')
  })
})
