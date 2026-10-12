// @vitest-environment jsdom
/**
 * Поле ввода при аккаунте без связи (D-37): служебная заметка доступна, а обычное сообщение,
 * вложения и голосовые — нет; брошенный файл не теряется молча; отказ 409 `account_offline`
 * заставляет чат перечитать своё состояние.
 *
 * Поле рисуется настоящим React в jsdom, сервер подменён.
 */

import { act, createElement, createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToasts } from '@/shared/ui'

import {
  FakeServer,
  click,
  errorReply,
  mount,
  settle,
  text,
  typeInto,
  unmount,
  type Mounted,
} from '../testKit'
import { Composer, type ComposerHandle } from './Composer'

const CHAT = 7
const REASON = 'Аккаунт отключён'

let server: FakeServer
let mounted: Mounted | null = null
const handle = createRef<ComposerHandle>()

async function show(disabledReason: string | null = null) {
  mounted = await mount(createElement(Composer, { ref: handle, conversationId: CHAT, disabledReason }))
}

const field = () => document.querySelector('textarea') as HTMLTextAreaElement
const button = (label: string) => document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)
const sendButton = () => button('Отправить')!
/** Файлы бросили на окно чата: ChatPage передаёт их полю ввода через этот метод. */
async function drop(files: File[]) {
  await act(async () => handle.current!.addFiles(files))
  await settle()
}
const posts = () => server.find('POST', `/api/v1/conversations/${CHAT}/messages`)

beforeEach(() => {
  window.sessionStorage.clear()
  useToasts.setState({ items: [] })
  server = new FakeServer()
  server.on('GET', '/api/v1/templates', { body: [] })
  server.on('POST', `/api/v1/conversations/${CHAT}/messages`, { body: { id: 1 } })
  server.install()
})

afterEach(async () => {
  await unmount(mounted)
  mounted = null
  vi.unstubAllGlobals()
  expect(server.unhandled, 'запросы без заглушки').toEqual([])
})

describe('связь есть', () => {
  it('всё доступно: поле, скрепка, заметка', async () => {
    await show()
    expect(field().disabled).toBe(false)
    expect(field().placeholder).toBe('Написать сообщение…')
    expect(button('Прикрепить файл')!.disabled).toBe(false)
    expect(button('Служебная заметка')!.disabled).toBe(false)
  })

  it('обычное сообщение уходит как обычное', async () => {
    await show()
    await typeInto(field(), 'Добрый день')
    await click(sendButton())
    expect(posts()).toHaveLength(1)
    expect(posts()[0].body).toMatchObject({ text: 'Добрый день', is_internal: false })
  })
})

describe('аккаунт без связи', () => {
  it('обычный режим: поле, скрепка и «Отправить» выключены, в поле сказано почему и что можно', async () => {
    await show(REASON)
    expect(field().disabled).toBe(true)
    expect(field().placeholder).toBe('Аккаунт отключён — только заметки')
    expect(button('Прикрепить файл')!.disabled).toBe(true)
    expect(sendButton().disabled).toBe(true)
    // Замочек работает — это и есть путь к заметке
    expect(button('Служебная заметка')!.disabled).toBe(false)
  })

  it('удалённый аккаунт назван удалённым в самом поле', async () => {
    await show('Аккаунт удалён')
    expect(field().placeholder).toBe('Аккаунт удалён — только заметки')
  })

  it('режим заметки: поле открыто, пишется и уходит как служебная заметка, клиенту — ничего', async () => {
    await show(REASON)
    await click(button('Служебная заметка'))

    expect(field().disabled).toBe(false)
    expect(field().placeholder).toBe('Заметка для команды…')
    expect(text()).toContain('Служебная заметка — клиент её не увидит')
    // Вложения и голосовые по-прежнему закрыты
    expect(button('Прикрепить файл')!.disabled).toBe(true)
    expect(button('Записать голосовое')).toBeNull()

    await typeInto(field(), 'Клиент просил перезвонить')
    expect(sendButton().disabled).toBe(false)
    await click(sendButton())

    expect(posts()).toHaveLength(1)
    expect(posts()[0].body).toMatchObject({ text: 'Клиент просил перезвонить', is_internal: true })
    expect(field().value).toBe('')
    // Без связи режим заметки остаётся: иначе поле снова выключалось бы после каждой заметки
    expect(field().disabled).toBe(false)
    expect(field().placeholder).toBe('Заметка для команды…')
  })

  it('вернули обычный режим — написанное остаётся, но уйти как сообщение клиенту не может', async () => {
    await show(REASON)
    await click(button('Служебная заметка'))
    await typeInto(field(), 'Текст')

    await click(button('Обычное сообщение'))

    expect(field().value).toBe('Текст')
    expect(field().disabled).toBe(true)
    expect(sendButton().disabled).toBe(true)
    await click(sendButton())
    expect(posts()).toHaveLength(0)
  })

  it('черновик, набранный до обрыва, обычным сообщением не отправить', async () => {
    window.sessionStorage.setItem(`crm-draft:${CHAT}`, 'Черновик до обрыва')
    await show(REASON)

    expect(field().value).toBe('Черновик до обрыва')
    expect(sendButton().disabled).toBe(true)
    await click(sendButton())
    expect(posts()).toHaveLength(0)
  })

  it('черновик можно превратить в заметку одним нажатием замочка', async () => {
    window.sessionStorage.setItem(`crm-draft:${CHAT}`, 'Черновик до обрыва')
    await show(REASON)

    await click(button('Служебная заметка'))
    await click(sendButton())

    expect(posts()[0].body).toMatchObject({ text: 'Черновик до обрыва', is_internal: true })
  })

  it('брошенный или вставленный файл не пропадает молча: уведомление с причиной, загрузки нет', async () => {
    const upload = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {})
    await show(REASON)

    await drop([new File(['x'], 'договор.pdf', { type: 'application/pdf' })])

    expect(useToasts.getState().items.map((item) => item.text)).toEqual([
      'Файл не прикреплён: аккаунт отключён, клиенту сейчас ничего не отправить',
    ])
    expect(upload).not.toHaveBeenCalled()
    expect(document.body.querySelector('ul[aria-label="Вложения к сообщению"]')).toBeNull()
    upload.mockRestore()
  })

  it('контроль: при живой связи тот же файл прикрепляется и грузится, уведомления нет', async () => {
    const upload = vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => {})
    await show()

    await drop([new File(['x'], 'договор.pdf', { type: 'application/pdf' })])

    expect(upload).toHaveBeenCalledTimes(1)
    expect(document.body.querySelector('ul[aria-label="Вложения к сообщению"]')).not.toBeNull()
    expect(useToasts.getState().items).toEqual([])
    upload.mockRestore()
  })

  it('пустое перетаскивание (не файлы) уведомления не вызывает', async () => {
    await show(REASON)
    await drop([])
    expect(useToasts.getState().items).toEqual([])
  })
})

describe('отказ сервера «аккаунт без связи» (409 account_offline)', () => {
  it('показан человеку и заставляет чат перечитать карточку и список', async () => {
    server.on(
      'POST',
      `/api/v1/conversations/${CHAT}/messages`,
      errorReply(409, 'account_offline', 'Аккаунт без связи — сообщение не отправлено. Сообщите руководителю'),
    )
    await show()
    const invalidate = vi.spyOn(mounted!.client, 'invalidateQueries')

    await typeInto(field(), 'Добрый день')
    await click(sendButton())

    expect(text()).toContain('Аккаунт без связи — сообщение не отправлено')
    const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
    expect(keys).toContain(JSON.stringify(['conversation', CHAT]))
    expect(keys).toContain(JSON.stringify(['conversations']))
    // Текст не потерян: его можно отправить, когда связь вернётся
    expect(field().value).toBe('Добрый день')
  })

  it('прочий конфликт карточку чата не перечитывает', async () => {
    server.on('POST', `/api/v1/conversations/${CHAT}/messages`, errorReply(409, 'conflict', 'Конфликт'))
    await show()
    const invalidate = vi.spyOn(mounted!.client, 'invalidateQueries')

    await typeInto(field(), 'Добрый день')
    await click(sendButton())

    expect(text()).toContain('Конфликт')
    expect(invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))).not.toContain(
      JSON.stringify(['conversation', CHAT]),
    )
  })
})
