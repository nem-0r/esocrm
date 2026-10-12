// @vitest-environment jsdom
/**
 * Чат при аккаунте без связи (D-37): баннер для менеджера и руководителя, кнопка
 * «Переподключить» только там, где она работает, заметка вместо обычного сообщения, брошенный на
 * чат файл и отказ сервера 409 `account_offline`, который сам обновляет состояние чата.
 *
 * Страница рисуется целиком настоящим React в jsdom; сервер подменён, как и то, чего в jsdom нет.
 */

import { act, createElement } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AccountStatus, Conversation, Message } from '@/entities/types'
import { useToasts } from '@/shared/ui'

const auth = vi.hoisted(() => ({
  me: { id: 1, full_name: 'Елена Руководитель', role: 'admin', is_admin: true, accepting_leads: true },
}))
vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useMe: () => auth.me,
}))
// Не предмет этих тестов (и чужие области): колокольчик, окно оплаты, окно передачи чата.
vi.mock('@/app/layout/SectionActions', () => ({ SectionActions: () => null }))
vi.mock('@/features/deals/DealSheet', () => ({ DealSheet: () => null }))
vi.mock('@/features/chats/components/TransferSheet', () => ({ TransferSheet: () => null }))

import { ChatPage } from './ChatPage'
import {
  FakeServer,
  byText,
  click,
  conversation,
  errorReply,
  mount,
  settle,
  stubBrowserGaps,
  text,
  typeInto,
  unmount,
  type Mounted,
} from './testKit'

const CHAT = 7
const COUNTERS = { total: 3, awaiting: 0, awaiting_payment: 0, over_threshold: 0, over_threshold_minutes: 30 }

let server: FakeServer
let mounted: Mounted | null = null
let chat: Conversation
let messages: Message[]

function setMe(role: 'admin' | 'manager') {
  auth.me = { ...auth.me, id: role === 'admin' ? 1 : 2, role, is_admin: role === 'admin' }
}

function setAccount(status: AccountStatus | undefined, reason: string | null = null) {
  chat = conversation(CHAT, {
    client_paid_amount: 0,
    client_paid_count: 0,
    account: { id: 2, title: 'Марина · допы', funnel_stage: 'sales', status, status_reason: reason },
  })
}

function failedMessage(): Message {
  return {
    id: 9,
    conversation_id: CHAT,
    direction: 'out',
    author_kind: 'manager',
    author: { id: 1, full_name: 'Елена Руководитель', avatar_color: '#7A6BE0' },
    kind: 'text',
    text: 'Добрый день',
    is_internal: false,
    status: 'failed',
    error_text: 'Не удалось отправить',
    created_at: '2026-10-11T09:00:00Z',
    sent_at: null,
    read_at: null,
    edited_at: null,
    reply_to_tg_id: null,
    attachments: [],
  } as Message
}

/** Куда увёл «Переподключить»: путь и параметры. */
function Landing() {
  const location = useLocation()
  return createElement('div', { id: 'landing' }, location.pathname + location.search)
}

async function show() {
  mounted = await mount(
    createElement(
      MemoryRouter,
      { initialEntries: [`/chats/${CHAT}`] },
      createElement(
        Routes,
        null,
        createElement(Route, { path: '/chats/:conversationId', element: createElement(ChatPage) }),
        createElement(Route, { path: '/profile/accounts', element: createElement(Landing) }),
      ),
    ),
  )
}

const banner = () => document.querySelector<HTMLElement>('[role="status"]')
const field = () => document.querySelector('textarea') as HTMLTextAreaElement
const sendButton = () => document.querySelector<HTMLButtonElement>('button[aria-label="Отправить"]')!
const reconnect = () => byText('button', 'Переподключить')
const toasts = () => useToasts.getState().items.map((item) => item.text)
const chatReads = () => server.find('GET', `/api/v1/conversations/${CHAT}`)

beforeEach(() => {
  stubBrowserGaps()
  setMe('admin')
  setAccount('connected')
  messages = []
  window.sessionStorage.clear()
  useToasts.setState({ items: [] })
  server = new FakeServer()
  server.on('GET', '/api/v1/conversations/counters', { body: COUNTERS })
  server.on('GET', '/api/v1/conversations', { body: { items: [conversation(11)], next_cursor: null } })
  server.on('GET', `/api/v1/conversations/${CHAT}`, () => ({ body: chat }))
  server.on('GET', `/api/v1/conversations/${CHAT}/messages`, () => ({ body: { items: messages, next_cursor: null } }))
  server.on('POST', `/api/v1/conversations/${CHAT}/read`, () => ({ body: chat }))
  server.on('GET', '/api/v1/templates', { body: [] })
  server.on('POST', `/api/v1/conversations/${CHAT}/messages`, { body: { id: 100 } })
  server.install()
})

afterEach(async () => {
  await unmount(mounted)
  mounted = null
  vi.unstubAllGlobals()
  expect(server.unhandled, 'запросы без заглушки').toEqual([])
})

describe('баннер: менеджер', () => {
  beforeEach(() => setMe('manager'))

  it('сообщает, что делать, и не даёт ни кнопки, ни ссылки', async () => {
    setAccount('error', 'Сессия отозвана')
    await show()

    expect(banner()!.textContent).toBe(
      'Аккаунт «Марина · допы» отключён — сообщения не уходят. Сообщите руководителю',
    )
    expect(reconnect()).toBeNull()
    expect(banner()!.querySelector('a, button')).toBeNull()
    // Причина — служебная подробность руководителя
    expect(text()).not.toContain('Сессия отозвана')
  })

  it('удалённый аккаунт назван удалённым', async () => {
    setAccount('disconnected')
    await show()
    expect(banner()!.textContent).toBe(
      'Аккаунт «Марина · допы» удалён — сообщения не уходят. Сообщите руководителю',
    )
    expect(reconnect()).toBeNull()
  })
})

describe('баннер: руководитель', () => {
  it('потерянная сессия: текст без «сообщите руководителю», причина и рабочая кнопка', async () => {
    setAccount('error', 'Сессия отозвана')
    await show()

    expect(banner()!.textContent).toContain('Аккаунт «Марина · допы» отключён — сообщения не уходят.')
    expect(banner()!.textContent).not.toContain('Сообщите руководителю')
    expect(banner()!.textContent).toContain('Причина: Сессия отозвана')
    expect(reconnect()).not.toBeNull()

    await click(reconnect())

    // Ведёт в «Аккаунты» сразу с фильтром «требуют внимания»
    expect(document.getElementById('landing')?.textContent).toBe('/profile/accounts?attention=1')
  })

  it('удалённый аккаунт: «Аккаунт удалён», кнопки нет — переподключать нечего', async () => {
    setAccount('disconnected')
    await show()

    expect(banner()!.textContent).toBe('Аккаунт «Марина · допы» удалён — сообщения не уходят.')
    expect(reconnect()).toBeNull()
    expect(banner()!.querySelector('button')).toBeNull()
    expect(text()).not.toContain('Причина')
  })

  it('вход не завершён: «не подключён» и кнопка есть', async () => {
    setAccount('pending')
    await show()
    expect(banner()!.textContent).toContain('не подключён')
    expect(reconnect()).not.toBeNull()
  })

  it('связь есть — баннера нет, поле открыто', async () => {
    await show()
    expect(banner()).toBeNull()
    expect(text()).not.toContain('сообщения не уходят')
    expect(field().disabled).toBe(false)
  })

  it('старый сервер статус не присылает — обрыв не выдумываем', async () => {
    setAccount(undefined)
    await show()
    expect(banner()).toBeNull()
    expect(field().disabled).toBe(false)
  })
})

describe('поле ввода под баннером', () => {
  it('обычное сообщение закрыто, но заметка пишется и уходит', async () => {
    setAccount('error')
    await show()

    expect(field().disabled).toBe(true)
    expect(field().placeholder).toBe('Аккаунт отключён — только заметки')
    expect(sendButton().disabled).toBe(true)

    await click(document.querySelector('button[aria-label="Служебная заметка"]'))
    await typeInto(field(), 'Позвонить клиенту завтра')
    await click(sendButton())

    const posts = server.find('POST', `/api/v1/conversations/${CHAT}/messages`)
    expect(posts).toHaveLength(1)
    expect(posts[0].body).toMatchObject({ text: 'Позвонить клиенту завтра', is_internal: true })
  })

  it('удалённый аккаунт: в поле так и написано', async () => {
    setAccount('disconnected')
    await show()
    expect(field().placeholder).toBe('Аккаунт удалён — только заметки')
  })
})

describe('отказ сервера 409 account_offline сам обновляет чат', () => {
  const OFFLINE = errorReply(409, 'account_offline', 'Аккаунт без связи — сообщение не отправлено. Сообщите руководителю')

  it('отправка: событие об обрыве потерялось — баннер появляется после ответа сервера', async () => {
    server.on('POST', `/api/v1/conversations/${CHAT}/messages`, () => {
      // К моменту отказа аккаунт уже без связи, а интерфейс об этом не знает
      setAccount('error')
      return OFFLINE
    })
    await show()
    expect(banner()).toBeNull()
    const readsBefore = chatReads().length

    await typeInto(field(), 'Добрый день')
    await click(sendButton())

    expect(chatReads().length).toBeGreaterThan(readsBefore)
    expect(banner()!.textContent).toContain('отключён — сообщения не уходят')
    expect(field().disabled).toBe(true)
    // Набранное не потеряно: превращается в заметку или ждёт связи
    expect(field().value).toBe('Добрый день')
  })

  it('«Повторить»: отказ показан уведомлением, а баннер и выключенное поле появляются сами', async () => {
    messages = [failedMessage()]
    server.on('POST', `/api/v1/conversations/${CHAT}/messages/9/retry`, () => {
      setAccount('error')
      return OFFLINE
    })
    await show()
    expect(banner()).toBeNull()
    const readsBefore = chatReads().length

    await click(byText('button', 'Повторить'))

    expect(toasts()).toEqual(['Аккаунт без связи — сообщение не отправлено. Сообщите руководителю'])
    expect(useToasts.getState().items[0].kind).toBe('error')
    expect(chatReads().length).toBeGreaterThan(readsBefore)
    expect(banner()).not.toBeNull()
    expect(field().disabled).toBe(true)
  })

  it('прочая ошибка «Повторить» карточку чата не перечитывает', async () => {
    messages = [failedMessage()]
    server.on('POST', `/api/v1/conversations/${CHAT}/messages/9/retry`, errorReply(500, 'boom', 'Сервер недоступен'))
    await show()
    const readsBefore = chatReads().length

    await click(byText('button', 'Повторить'))

    expect(toasts()).toEqual(['Сервер недоступен'])
    expect(chatReads()).toHaveLength(readsBefore)
    expect(banner()).toBeNull()
  })
})

describe('файл, брошенный на чат', () => {
  function dropEvent(type: 'dragenter' | 'drop', files: File[]): Event {
    const event = new Event(type, { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'dataTransfer', { value: { files, types: ['Files'] } })
    return event
  }

  async function fire(target: Element, event: Event) {
    await act(async () => {
      target.dispatchEvent(event)
    })
    await settle()
  }

  // Шапка именно чата (у списка слева тоже есть <header>): событие всплывает до окна чата.
  const chatHeader = () => document.querySelector('a[aria-label="Карточка клиента"]')!
  const pdf = () => new File(['x'], 'договор.pdf', { type: 'application/pdf' })

  it('аккаунт без связи: не молча — уведомление с причиной; подсказки «отпустите» нет', async () => {
    setAccount('error')
    const upload = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {})
    await show()
    const target = chatHeader()

    await fire(target, dropEvent('dragenter', [pdf()]))
    expect(text()).not.toContain('Отпустите, чтобы прикрепить')

    await fire(target, dropEvent('drop', [pdf()]))
    expect(toasts()).toEqual(['Файл не прикреплён: аккаунт отключён, клиенту сейчас ничего не отправить'])
    expect(upload).not.toHaveBeenCalled()
    upload.mockRestore()
  })

  it('удалённый аккаунт: причина названа точно', async () => {
    setAccount('disconnected')
    await show()
    await fire(chatHeader(), dropEvent('drop', [pdf()]))
    expect(toasts()).toEqual(['Файл не прикреплён: аккаунт удалён, клиенту сейчас ничего не отправить'])
  })

  it('контроль: при живой связи подсказка «отпустите» есть и файл прикрепляется', async () => {
    const upload = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {})
    await show()
    const target = chatHeader()

    await fire(target, dropEvent('dragenter', [pdf()]))
    expect(text()).toContain('Отпустите, чтобы прикрепить')

    await fire(target, dropEvent('drop', [pdf()]))
    expect(toasts()).toEqual([])
    expect(upload).toHaveBeenCalledTimes(1)
    upload.mockRestore()
  })
})
