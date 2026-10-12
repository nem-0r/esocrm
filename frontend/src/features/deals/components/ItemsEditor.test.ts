// @vitest-environment jsdom
/**
 * Состав сделки, как его видит менеджер (BUG-07, L3).
 *
 * Библиотеки тестирования компонентов в проекте нет, поэтому редактор рисуется настоящим
 * React в jsdom. Проверяем то, что нельзя увидеть по одним функциям: убранная услуга
 * остаётся на экране приглушённой и зачёркнутой, «Вернуть» работает, «Убрать» есть,
 * пока активных строк больше одной, а у ошибки суммы — свой текст под полем.
 */

import { act, createElement, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { newItemDraft, type ItemDraft } from '@/features/deals/lib'

vi.mock('@/features/services/queries', () => ({
  useServices: () => ({ data: [], isLoading: false, isError: false }),
}))
vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useAuth: () => ({ isAdmin: false }),
}))

import { ItemsEditor } from './ItemsEditor'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

let root: Root
let host: HTMLElement
let latest: ItemDraft[]

function Harness({ initial }: { initial: ItemDraft[] }) {
  const [items, setItems] = useState(initial)
  latest = items
  return createElement(ItemsEditor, { items, onChange: setItems })
}

function row(name: string, amount: string): ItemDraft {
  return { ...newItemDraft(), name, amount }
}

async function show(initial: ItemDraft[]) {
  await act(async () => {
    root.render(
      createElement(MemoryRouter, null, createElement(Harness, { initial })),
    )
  })
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
})

const text = () => document.body.textContent ?? ''
const spaces = (value: string) => value.replace(/[\s\u00a0\u202f]/g, ' ')

function buttons(label: string): HTMLButtonElement[] {
  return Array.from(document.body.querySelectorAll<HTMLButtonElement>('button')).filter(
    (button) => (button.getAttribute('aria-label') ?? button.textContent ?? '').startsWith(label),
  )
}

function amountInputs(): HTMLInputElement[] {
  return Array.from(document.body.querySelectorAll<HTMLInputElement>('input')).filter(
    (input) => input.placeholder === 'Стоимость, ₽',
  )
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  })
}

async function typeAmount(index: number, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  const field = amountInputs()[index]
  await act(async () => {
    setter.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function leave(index: number) {
  await act(async () => {
    amountInputs()[index].dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
  })
}

describe('ItemsEditor: убрать и вернуть услугу', () => {
  it('«Убрать» есть у каждой строки, пока их больше одной; у единственной — нет', async () => {
    await show([row('Таро', '4900')])
    expect(buttons('Убрать услугу')).toHaveLength(0)
    await act(async () => root.unmount())
    root = createRoot(host)
    await show([row('Таро', '4900'), row('Натальная карта', '3500')])
    expect(buttons('Убрать услугу')).toHaveLength(2)
  })

  it('убранная услуга остаётся на месте, зачёркнутая, и её можно вернуть', async () => {
    await show([row('Таро', '4900'), row('Натальная карта', '3500')])
    expect(text()).toContain('Услуги · 2')

    await click(buttons('Убрать услугу')[1])
    // Строка не пропала: название и цена на экране, зачёркнутые.
    const struck = Array.from(document.body.querySelectorAll('.line-through')).map(
      (node) => node.textContent ?? '',
    )
    expect(struck.some((value) => value.includes('Натальная карта'))).toBe(true)
    expect(struck.some((value) => spaces(value).includes('3 500 ₽'))).toBe(true)
    expect(text()).toContain('Услуги · 1')
    expect(latest.map((item) => item.removed)).toEqual([false, true])
    // Последнюю активную строку убрать нельзя — кнопки у неё нет.
    expect(buttons('Убрать услугу')).toHaveLength(0)
    // Поля убранной строки скрыты: редактировать нечего.
    expect(amountInputs()).toHaveLength(1)

    const restore = buttons('Вернуть')
    expect(restore).toHaveLength(1)
    expect(restore[0].getAttribute('aria-label')).toBe('Вернуть услугу «Натальная карта»')
    await click(restore[0])

    expect(text()).toContain('Услуги · 2')
    expect(latest.map((item) => item.removed)).toEqual([false, false])
    expect(document.body.querySelectorAll('.line-through')).toHaveLength(0)
    expect(amountInputs()).toHaveLength(2)
    expect(amountInputs()[1].value).toBe('3500')
    expect(buttons('Вернуть')).toHaveLength(0)
    expect(buttons('Убрать услугу')).toHaveLength(2)
  })

  it('убрать можно и первую строку; порядок строк не меняется', async () => {
    await show([row('Таро', '4900'), row('Натальная карта', '3500'), row('Синастрия', '2000')])
    await click(buttons('Убрать услугу')[0])
    expect(latest.map((item) => [item.name, item.removed])).toEqual([
      ['Таро', true],
      ['Натальная карта', false],
      ['Синастрия', false],
    ])
    expect(text()).toContain('Услуги · 2')
  })
})

describe('ItemsEditor: у ошибки суммы своя причина под полем', () => {
  it('пустое поле молчит, пока человек его не покинул', async () => {
    await show([newItemDraft()])
    expect(document.body.querySelector('[role="alert"]')).toBeNull()
    await leave(0)
    expect(document.body.querySelector('[role="alert"]')?.textContent).toBe('Введите сумму')
  })

  it.each([
    ['10.555', 'Не больше двух знаков после запятой'],
    ['0', 'Стоимость должна быть больше нуля'],
    ['100000000,01', 'Сумма не больше 100 000 000 ₽'],
    ['абв', 'Введите сумму цифрами, например 4500 или 4500,50'],
  ])('«%s» → «%s»', async (typed, expected) => {
    await show([newItemDraft()])
    await typeAmount(0, typed)
    const alert = document.body.querySelector('[role="alert"]')
    expect(spaces(alert?.textContent ?? '')).toBe(expected)
    expect(amountInputs()[0].getAttribute('aria-invalid')).toBe('true')
  })

  it('верная сумма — без ошибки, а копейки и сумма прописью-цифрами видны справа', async () => {
    await show([newItemDraft()])
    await typeAmount(0, '4 900,50')
    expect(document.body.querySelector('[role="alert"]')).toBeNull()
    expect(spaces(text())).toContain('4 900,50 ₽')
  })
})

async function enter(index: number) {
  await act(async () => {
    amountInputs()[index].dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
  })
}

const alertText = () => document.body.querySelector('[role="alert"]')?.textContent ?? null

describe('ItemsEditor: «4500,» в процессе набора — не ошибка, пока курсор в поле', () => {
  it('на каждом нажатии по пути к «4500,50» красное сообщение не появляется', async () => {
    await show([newItemDraft()])
    await enter(0)
    for (const typed of ['4', '45', '450', '4500', '4500,', '4500,5', '4500,50']) {
      await typeAmount(0, typed)
      expect(alertText(), typed).toBeNull()
      expect(amountInputs()[0].getAttribute('aria-invalid'), typed).toBe('false')
    }
  })

  it('точка вместо запятой и пробел в тысячах — то же', async () => {
    await show([newItemDraft()])
    await enter(0)
    await typeAmount(0, '4500.')
    expect(alertText()).toBeNull()
    await typeAmount(0, '4 500,')
    expect(alertText()).toBeNull()
  })

  it('ушли из поля с «4500,» — это ошибка, сообщение появляется один раз', async () => {
    await show([newItemDraft()])
    await enter(0)
    await typeAmount(0, '4500,')
    expect(alertText()).toBeNull()

    await leave(0)
    expect(alertText()).toBe('Введите сумму цифрами, например 4500 или 4500,50')
    expect(amountInputs()[0].getAttribute('aria-invalid')).toBe('true')
    expect(document.body.querySelectorAll('[role="alert"]')).toHaveLength(1)
  })

  it('вернулись в поле и дописали — ошибка уходит', async () => {
    await show([newItemDraft()])
    await enter(0)
    await typeAmount(0, '4500,')
    await leave(0)
    expect(alertText()).not.toBeNull()

    await enter(0)
    await typeAmount(0, '4500,5')
    expect(alertText()).toBeNull()
  })

  it('снова печатают «4500,» после ухода из поля — сообщение не вспыхивает заново', async () => {
    await show([newItemDraft()])
    await enter(0)
    await typeAmount(0, '4500')
    await leave(0)
    await enter(0)
    await typeAmount(0, '4500,')
    expect(alertText()).toBeNull()
  })

  it('настоящие ошибки видны сразу, даже пока курсор в поле', async () => {
    await show([newItemDraft()])
    await enter(0)
    await typeAmount(0, 'абв')
    expect(alertText()).toBe('Введите сумму цифрами, например 4500 или 4500,50')
    await typeAmount(0, '4500,,')
    expect(alertText()).toBe('Введите сумму цифрами, например 4500 или 4500,50')
    await typeAmount(0, '10.555')
    expect(alertText()).toBe('Не больше двух знаков после запятой')
  })
})
