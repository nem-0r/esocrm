// @vitest-environment jsdom
/**
 * Запросы раздела «Чаты»: пересылка при аккаунте без связи (409 `account_offline`) и
 * «Взять первого в очереди» (`takeNextInQueue`).
 *
 * Отправка и «Повторить» проверены на целых экранах (`Composer.test.ts`, `ChatPage.test.ts`);
 * здесь — пересылка, у которой экрана в этих тестах нет, и сама очередь без интерфейса.
 */

import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useForwardMessages, takeNextInQueue } from './queries'
import { FakeServer, click, conversation, errorReply, mount, unmount, type Mounted } from './testKit'

let server: FakeServer
let mounted: Mounted | null = null

beforeEach(() => {
  server = new FakeServer()
  server.install()
})

afterEach(async () => {
  await unmount(mounted)
  mounted = null
  vi.unstubAllGlobals()
  expect(server.unhandled, 'запросы без заглушки').toEqual([])
})

describe('пересылка в чат на аккаунте без связи', () => {
  function Forwarder() {
    const forward = useForwardMessages()
    return createElement(
      'button',
      {
        id: 'forward',
        onClick: () =>
          forward.mutate({ targetId: 9, source_conversation_id: 7, message_ids: [1], hide_sender: true }),
      },
      'переслать',
    )
  }

  async function press() {
    mounted = await mount(createElement(Forwarder))
    const invalidate = vi.spyOn(mounted.client, 'invalidateQueries')
    await click(document.getElementById('forward'))
    return invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
  }

  it('409 account_offline: перечитываются карточка чата-получателя и список', async () => {
    server.on('POST', '/api/v1/conversations/9/forward', errorReply(409, 'account_offline', 'Аккаунт без связи'))
    const keys = await press()
    expect(keys).toContain(JSON.stringify(['conversation', 9]))
    expect(keys).toContain(JSON.stringify(['conversations']))
  })

  it('прочая ошибка карточку не перечитывает', async () => {
    server.on('POST', '/api/v1/conversations/9/forward', errorReply(409, 'conflict', 'Конфликт'))
    const keys = await press()
    expect(keys).not.toContain(JSON.stringify(['conversation', 9]))
  })

  it('успех обновляет сообщения получателя, а не карточку', async () => {
    server.on('POST', '/api/v1/conversations/9/forward', { body: { conversation_id: 9, messages: [], mode: 'native' } })
    const keys = await press()
    expect(keys).toContain(JSON.stringify(['messages', 9]))
    expect(keys).not.toContain(JSON.stringify(['conversation', 9]))
  })
})

describe('takeNextInQueue', () => {
  const T1 = '2026-10-11T09:00:00Z'
  const T2 = '2026-10-11T12:00:00Z'
  const lookups = () => server.find('GET', '/api/v1/conversations')
  const nexts = () => server.find('GET', '/api/v1/conversations/next')

  it('взятых нет — один запрос, чат запоминается вместе со временем ожидания', async () => {
    server.on('GET', '/api/v1/conversations/next', { body: conversation(5, { awaiting_reply_since: T1 }) })
    const result = await takeNextInQueue([], true)
    expect(lookups()).toHaveLength(0)
    expect(nexts()[0].query.has('skip')).toBe(false)
    expect(result.conversation.id).toBe(5)
    expect(result.taken).toEqual([{ id: 5, since: T1 }])
  })

  it('никто не ждёт — спрашивать, кто ждёт, незачем', async () => {
    server.on('GET', '/api/v1/conversations/next', { body: conversation(6) })
    const result = await takeNextInQueue([{ id: 5, since: T1 }], false)
    expect(lookups()).toHaveLength(0)
    expect(nexts()[0].query.get('skip')).toBe('5')
    expect(result.taken).toEqual([{ id: 5, since: T1 }, { id: 6, since: null }])
  })

  it('спрашивает ждущих (давние первыми, до 100) и убирает написавших снова', async () => {
    server.on('GET', '/api/v1/conversations', {
      body: {
        items: [conversation(5, { awaiting_reply_since: T2 }), conversation(8, { awaiting_reply_since: T1 })],
        next_cursor: null,
      },
    })
    server.on('GET', '/api/v1/conversations/next', { body: conversation(5, { awaiting_reply_since: T2 }) })

    const result = await takeNextInQueue(
      [
        { id: 5, since: T1 }, // ответили, клиент написал снова — в очередь
        { id: 8, since: T1 }, // всё ещё ждёт с того же момента — «уже открывал»
      ],
      true,
    )

    expect(lookups()[0].query.get('filter')).toBe('awaiting')
    expect(lookups()[0].query.get('limit')).toBe('100')
    expect(nexts()[0].query.get('skip')).toBe('8')
    expect(result.taken).toEqual([
      { id: 8, since: T1 },
      { id: 5, since: T2 },
    ])
  })

  it('не вышло спросить — идём со своим списком, кнопка не ломается', async () => {
    server.on('GET', '/api/v1/conversations', errorReply(500, 'boom', 'Сервер недоступен'))
    server.on('GET', '/api/v1/conversations/next', { body: conversation(6, { awaiting_reply_since: T2 }) })
    const result = await takeNextInQueue([{ id: 5, since: T1 }], true)
    expect(nexts()[0].query.get('skip')).toBe('5')
    expect(result.conversation.id).toBe(6)
  })

  it('очередь пройдена (404 queue_done) — ошибка доходит до экрана как есть', async () => {
    server.on('GET', '/api/v1/conversations/next', errorReply(404, 'queue_done', 'queue_done'))
    await expect(takeNextInQueue([], true)).rejects.toMatchObject({ status: 404, code: 'queue_done' })
  })
})
