/**
 * Что реально попадает в разметку: значок статуса в превью чата и в пузыре.
 * Рендер на сервере (react-dom/server) — без браузера: проверяем то, что видит
 * человек и скринридер, — подпись, `title` и наличие значка.
 */

import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { StaticRouter } from 'react-router-dom/server'
import { describe, expect, it } from 'vitest'

import type { Conversation } from '@/entities/types'

import { ChatListItem } from './ChatListItem'
import { MessageStatusIcon } from './MessageStatusIcon'

function conversation(patch: Partial<Conversation>): Conversation {
  return {
    id: 7,
    client: { id: 3, name: 'Ксения', phone: null, tg_username: null, data_complete: false },
    account: { id: 2, title: 'Марина · допы', funnel_stage: 'sales' },
    last_message_at: '2026-10-11T09:00:00Z',
    last_message_preview: 'Вы: Здравствуйте',
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

function renderItem(c: Conversation): string {
  return renderToStaticMarkup(
    createElement(StaticRouter, { location: '/chats' }, createElement(ChatListItem, { conversation: c })),
  )
}

describe('MessageStatusIcon', () => {
  it.each([
    ['queued', 'Отправляется'],
    ['sent', 'Отправлено'],
    ['read', 'Прочитано'],
    ['failed', 'Не доставлено'],
  ] as const)('%s → подпись «%s» в aria-label и title', (status, label) => {
    const html = renderToStaticMarkup(createElement(MessageStatusIcon, { status }))
    expect(html).toContain(`aria-label="${label}"`)
    expect(html).toContain(`title="${label}"`)
    expect(html).toContain('role="img"')
  })

  it('красный значок только у «не доставлено»', () => {
    const failed = renderToStaticMarkup(createElement(MessageStatusIcon, { status: 'failed' }))
    const sent = renderToStaticMarkup(createElement(MessageStatusIcon, { status: 'sent' }))
    expect(failed).toContain('text-danger')
    expect(sent).not.toContain('text-danger')
  })

  it('входящее и пустой статус — ничего', () => {
    expect(
      renderToStaticMarkup(createElement(MessageStatusIcon, { status: 'read', direction: 'in' })),
    ).toBe('')
    expect(renderToStaticMarkup(createElement(MessageStatusIcon, { status: null }))).toBe('')
  })
})

describe('превью в списке чатов', () => {
  it('у последнего исходящего значок стоит перед временем', () => {
    const html = renderItem(
      conversation({ last_message_direction: 'out', last_message_status: 'failed' }),
    )
    expect(html).toContain('aria-label="Не доставлено"')
    // Внутри того же блока, что и время, и раньше него: `<значок></span>время</span>`.
    expect(html).toMatch(/title="Не доставлено".*?<\/svg><\/span>[^<]+<\/span>/)
  })

  it('у входящего значка нет', () => {
    const html = renderItem(
      conversation({
        last_message_preview: 'Спасибо!',
        last_message_direction: 'in',
        last_message_status: null,
      }),
    )
    expect(html).not.toContain('role="img"')
  })

  it('старый сервер новых полей не шлёт — строка рисуется без значка и без ошибок', () => {
    const html = renderItem(conversation({}))
    expect(html).toContain('Вы: Здравствуйте')
    expect(html).not.toContain('role="img"')
  })
})
