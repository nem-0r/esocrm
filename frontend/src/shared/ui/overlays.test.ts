// @vitest-environment jsdom
/**
 * Общее окно `Sheet`: отступ снизу и закрытие по Escape.
 *
 * Окно выезжает снизу экрана и стоит прямо на его нижнем крае. Если у него нет footer,
 * последней в нём оказывается прокручиваемая часть, и на iPhone последняя строка
 * (например, «Показать ещё») упиралась бы в home-индикатор. В jsdom нет ни стилей, ни выреза,
 * поэтому проверяется, что нужный класс стоит там, где нужно; что он даёт
 * `padding-bottom: max(1rem, env(safe-area-inset-bottom))`, проверено сборкой Tailwind.
 */

import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmDialog, Sheet } from './overlays'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const SAFE_BOTTOM = 'pb-[max(1rem,env(safe-area-inset-bottom))]'

let root: Root
let host: HTMLElement

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

async function render(node: ReturnType<typeof createElement>) {
  await act(async () => {
    root.render(node)
  })
}

const dialog = () => document.body.querySelector('[role="dialog"]') as HTMLElement
/** Прокручиваемая часть окна — та, где лежит содержимое. */
const body = () => dialog().querySelector('.overflow-y-auto') as HTMLElement

describe('Sheet: отступ снизу', () => {
  it('без footer тело держит отступ с учётом выреза iPhone', async () => {
    await render(
      createElement(Sheet, {
        open: true,
        onOpenChange: () => {},
        title: 'Уведомления',
        children: createElement('button', null, 'Показать ещё'),
      }),
    )
    expect(body().classList.contains(SAFE_BOTTOM)).toBe(true)
    // Обычный pb-4 рядом стёр бы вырез: оба задают один и тот же padding-bottom.
    expect(body().classList.contains('pb-4')).toBe(false)
    // На десктопе окно по центру, выреза нет — обычный отступ.
    expect(body().classList.contains('desk:pb-4')).toBe(true)
    expect(body().textContent).toBe('Показать ещё')
  })

  it('с footer тело держит обычный отступ, а вырез учитывает сам footer', async () => {
    await render(
      createElement(Sheet, {
        open: true,
        onOpenChange: () => {},
        title: 'Период',
        footer: createElement('button', null, 'Применить'),
        children: createElement('p', null, 'Содержимое'),
      }),
    )
    expect(body().classList.contains('pb-4')).toBe(true)
    expect(body().classList.contains(SAFE_BOTTOM)).toBe(false)
    const footer = Array.from(dialog().querySelectorAll('div')).find((node) =>
      node.textContent === 'Применить' && node.classList.contains('border-t'),
    )
    expect(footer?.classList.contains('pb-safe-3')).toBe(true)
  })

  it('ConfirmDialog (у него есть кнопки внизу) — обычный отступ тела', async () => {
    await render(
      createElement(ConfirmDialog, {
        open: true,
        onOpenChange: () => {},
        title: 'Удалить?',
        onConfirm: () => {},
      }),
    )
    expect(body().classList.contains('pb-4')).toBe(true)
    expect(body().classList.contains(SAFE_BOTTOM)).toBe(false)
  })
})

describe('Sheet: Escape', () => {
  async function renderWithCombobox(onOpenChange: (open: boolean) => void, expanded: boolean) {
    await render(
      createElement(Sheet, {
        open: true,
        onOpenChange,
        title: 'Оплата',
        children: createElement('input', {
          'data-testid': 'service',
          role: 'combobox',
          'aria-expanded': expanded,
          'aria-controls': 'x',
        }),
      }),
    )
  }

  async function pressEscape(target: Element) {
    await act(async () => {
      target.dispatchEvent(
        new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
      )
    })
  }

  it('Escape в раскрытом списке услуг не закрывает всё окно', async () => {
    const onOpenChange = vi.fn()
    await renderWithCombobox(onOpenChange, true)
    await pressEscape(document.querySelector('[data-testid="service"]') as Element)
    expect(onOpenChange).not.toHaveBeenCalled()
  })

  it('Escape в свёрнутом поле закрывает окно', async () => {
    const onOpenChange = vi.fn()
    await renderWithCombobox(onOpenChange, false)
    await pressEscape(document.querySelector('[data-testid="service"]') as Element)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('Escape, пришедший не от элемента (событие на document), не роняет окно и закрывает его', async () => {
    const onOpenChange = vi.fn()
    await renderWithCombobox(onOpenChange, false)
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
