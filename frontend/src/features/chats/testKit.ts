/**
 * Общая оснастка для тестов раздела «Чаты» (jsdom + настоящий React, сервер подменён).
 *
 * Не тест сам по себе (`.test.ts` в имени нет) — vitest его не запускает, только подключают
 * `ChatsLayout.test.ts`, `ChatPage.test.ts`, `Composer.test.ts`. Страницы рисуются целиком, как
 * в приложении: хуки запросов, роутер, кэш — настоящие; подменены только `fetch` и браузерные
 * возможности, которых нет в jsdom.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement, type ReactElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { expect, vi } from 'vitest'

import type { Conversation } from '@/entities/types'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

export interface Call {
  method: string
  path: string
  query: URLSearchParams
  body: unknown
}

export interface Reply {
  status?: number
  body?: unknown
}

interface Route {
  method: string
  match: string | RegExp
  reply: Reply | ((call: Call) => Reply)
}

/** Строка списка чатов / карточка чата — с разумными значениями, нужное переопределяется. */
export function conversation(id: number, patch: Partial<Conversation> = {}): Conversation {
  return {
    id,
    client: { id: 100 + id, name: `Клиент ${id}`, phone: null, tg_username: null, data_complete: false },
    account: { id: 2, title: 'Марина · допы', funnel_stage: 'sales', status: 'connected', status_reason: null },
    last_message_at: '2026-10-11T09:00:00Z',
    last_message_preview: 'Здравствуйте',
    unread_count: 0,
    awaiting_reply_since: null,
    awaiting_minutes: null,
    responsible: null,
    has_awaiting_deal: false,
    awaiting_deal_amount: null,
    is_blocked_by_client: false,
    ...patch,
  } as Conversation
}

export function errorReply(status: number, code: string, message: string): Reply {
  return { status, body: { error: { code, message, details: {} } } }
}

/** Поддельный сервер: отвечает по правилам, запоминает все запросы. Последнее правило главнее. */
export class FakeServer {
  calls: Call[] = []
  /** Запросы, на которые правила не нашлось, — тест не должен их оставлять. */
  unhandled: string[] = []
  private routes: Route[] = []

  on(method: string, match: string | RegExp, reply: Reply | ((call: Call) => Reply)): this {
    this.routes.unshift({ method, match, reply })
    return this
  }

  install(): void {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init?: RequestInit) => {
        const url = new URL(input, 'http://localhost')
        const method = init?.method ?? 'GET'
        const body = init?.body && typeof init.body === 'string' ? JSON.parse(init.body) : undefined
        const call: Call = { method, path: url.pathname, query: url.searchParams, body }
        this.calls.push(call)
        const route = this.routes.find(
          (candidate) =>
            candidate.method === method &&
            (typeof candidate.match === 'string'
              ? candidate.match === url.pathname
              : candidate.match.test(url.pathname)),
        )
        if (!route) {
          this.unhandled.push(`${method} ${url.pathname}`)
          return jsonResponse(errorReply(404, 'unhandled', `нет заглушки: ${method} ${url.pathname}`))
        }
        const reply = typeof route.reply === 'function' ? route.reply(call) : route.reply
        return jsonResponse(reply)
      }),
    )
  }

  /** Запросы по методу и пути. */
  find(method: string, path: string | RegExp): Call[] {
    return this.calls.filter(
      (call) =>
        call.method === method &&
        (typeof path === 'string' ? call.path === path : path.test(call.path)),
    )
  }
}

function jsonResponse(reply: Reply): Response {
  const status = reply.status ?? 200
  if (status === 204) return new Response(null, { status })
  return new Response(JSON.stringify(reply.body ?? {}), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Чего нет в jsdom, но нужно чату: наблюдатель за размером и прокрутка к элементу.
 * Подменяются на пустышки — для проверки разметки и действий они не важны.
 */
export function stubBrowserGaps(): void {
  class FakeResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  Element.prototype.scrollIntoView = () => {}
}

export const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** Дать React и промисам отработать: несколько оборотов цикла событий внутри act. */
export async function settle(turns = 8): Promise<void> {
  for (let i = 0; i < turns; i += 1) {
    await act(async () => {
      await sleep(0)
    })
  }
}

/** Подождать настоящее время (например, паузу поиска) внутри act, чтобы React не ругался. */
export async function advance(ms: number): Promise<void> {
  await act(async () => {
    await sleep(ms)
  })
  await settle()
}

export interface Mounted {
  host: HTMLElement
  root: Root
  client: QueryClient
}

/** Один смонтированный экран; `unmount` обязателен в `afterEach`. */
export async function mount(element: ReactElement): Promise<Mounted> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(createElement(QueryClientProvider, { client }, element))
  })
  await settle()
  return { host, root, client }
}

export async function unmount(mounted: Mounted | null): Promise<void> {
  if (!mounted) return
  await act(async () => mounted.root.unmount())
  mounted.host.remove()
  document.body.innerHTML = ''
}

export const text = () => document.body.textContent ?? ''

export function byText<T extends HTMLElement = HTMLElement>(selector: string, needle: string): T | null {
  const nodes = Array.from(document.body.querySelectorAll<T>(selector))
  return nodes.find((node) => node.textContent?.includes(needle)) ?? null
}

export async function click(el: Element | null): Promise<void> {
  expect(el, 'элемент для клика не найден').not.toBeNull()
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }))
  })
  await settle()
}

/** Ввести текст в поле так, как это видит React (через родной setter значения). */
export async function typeInto(field: HTMLInputElement | HTMLTextAreaElement, value: string): Promise<void> {
  const proto = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  await act(async () => {
    setter.call(field, value)
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
