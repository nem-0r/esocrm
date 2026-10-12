// @vitest-environment jsdom
/**
 * Список чатов (D-34 и замечания ревью): баннер и чип «Ждут >N мин», «Взять первого в очереди»,
 * подпись «Показано N из M» и сохранение текста поиска.
 *
 * Раздел рисуется настоящим React в jsdom: два маршрута, как в приложении (`/chats` и
 * `/chats/:id` — разные элементы, раздел пересобирается при переходе между ними), сервер
 * подменён. Проверяется то, что видит человек: разметка, адрес, запросы и уведомления.
 */

import { Fragment, createElement } from 'react'
import { Link, MemoryRouter, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToasts } from '@/shared/ui'

vi.mock('@/shared/hooks/useAuth', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/hooks/useAuth')>()),
  useMe: () => ({ id: 1, full_name: 'Елена Руководитель', role: 'admin', is_admin: true, accepting_leads: true }),
}))
// Колокольчик и поиск в шапке — не предмет этих тестов (и чужая область).
vi.mock('@/app/layout/SectionActions', () => ({ SectionActions: () => null }))

import { ChatsLayout } from './ChatsLayout'
import {
  FakeServer,
  advance,
  byText,
  click,
  conversation,
  errorReply,
  mount,
  text,
  typeInto,
  unmount,
  type Mounted,
} from './testKit'

const COUNTERS = { total: 12, awaiting: 3, awaiting_payment: 1, over_threshold: 2, over_threshold_minutes: 30 }
const T1 = '2026-10-11T09:00:00Z'
const T2 = '2026-10-11T12:00:00Z'

let server: FakeServer
let mounted: Mounted | null = null
let counters = COUNTERS

function Section() {
  const { conversationId } = useParams()
  return createElement(
    ChatsLayout,
    { selectedId: Number(conversationId) },
    createElement('div', { id: 'chat-pane' }, `Чат ${conversationId}`),
  )
}

/** Адрес и кнопки «назад»/«в раздел» вне маршрутов — чтобы читать и двигать историю. */
function Probe() {
  const location = useLocation()
  const navigate = useNavigate()
  return createElement(
    'div',
    null,
    createElement('span', { id: 'loc' }, decodeURIComponent(location.pathname + location.search)),
    createElement('button', { id: 'probe-back', onClick: () => navigate(-1) }, 'probe-back'),
    createElement(Link, { id: 'probe-chats', to: '/chats' }, 'probe-chats'),
    createElement(Link, { id: 'probe-chat12', to: '/chats/12' }, 'probe-chat12'),
  )
}

async function open(url = '/chats') {
  mounted = await mount(
    createElement(
      MemoryRouter,
      { initialEntries: [url] },
      createElement(
        Fragment,
        null,
        createElement(Probe),
        createElement(
          Routes,
          null,
          createElement(Route, { path: '/chats', element: createElement(ChatsLayout, { selectedId: null }) }),
          createElement(Route, { path: '/chats/:conversationId', element: createElement(Section) }),
        ),
      ),
    ),
  )
}

const loc = () => document.getElementById('loc')?.textContent
const searchBox = () => document.querySelector<HTMLInputElement>('input[placeholder^="Поиск по чатам"]')!
const takeButton = () => byText<HTMLButtonElement>('button', 'Взять первого в очереди')
const toasts = () => useToasts.getState().items.map((item) => item.text)
const listCalls = () => server.find('GET', '/api/v1/conversations').filter((c) => c.query.get('limit') === '30')
const lookupCalls = () => server.find('GET', '/api/v1/conversations').filter((c) => c.query.get('limit') === '100')
const nextCalls = () => server.find('GET', '/api/v1/conversations/next')
const takenStored = () => window.sessionStorage.getItem('crm.chats.taken.1')

beforeEach(() => {
  window.sessionStorage.clear()
  useToasts.setState({ items: [] })
  counters = COUNTERS
  server = new FakeServer()
  server.on('GET', '/api/v1/conversations/counters', () => ({ body: counters }))
  server.on('GET', '/api/v1/conversations', () => ({
    body: { items: [conversation(11), conversation(12)], next_cursor: null },
  }))
  server.install()
})

afterEach(async () => {
  await unmount(mounted)
  mounted = null
  vi.unstubAllGlobals()
  expect(server.unhandled, 'запросы без заглушки').toEqual([])
})

describe('баннер и чип «Ждут >N мин»', () => {
  it('баннер называет число и порог; клик включает фильтр, а баннер уступает место чипу', async () => {
    await open()
    expect(text()).toContain('2 клиента ждут больше 30 минут')
    expect(byText('button', 'Ждут >30 мин')).toBeNull()

    await click(byText('button', 'клиента ждут больше 30 минут'))

    // Фильтр виден выбранным чипом с числом, баннер скрыт, список запрошен по фильтру
    const chip = byText('button', 'Ждут >30 мин')
    expect(chip).not.toBeNull()
    expect(chip!.textContent).toContain('2')
    expect(text()).not.toContain('ждут больше 30 минут')
    expect(listCalls().at(-1)!.query.get('filter')).toBe('awaiting_over')
    // …и переживает пересборку раздела (другой маршрут)
    expect(window.sessionStorage.getItem('crm.chats.filter')).toBe('awaiting_over')
  })

  it('крестик на чипе снимает фильтр: список снова «Все», баннер возвращается', async () => {
    window.sessionStorage.setItem('crm.chats.filter', 'awaiting_over')
    await open()

    const cross =
      Array.from(document.querySelectorAll('button')).find(
        (button) => button.getAttribute('aria-label') === 'Снять фильтр «Ждут >30 мин»',
      ) ?? null
    expect(cross).not.toBeNull()
    await click(cross)

    expect(byText('button', 'Ждут >30 мин')).toBeNull()
    expect(text()).toContain('2 клиента ждут больше 30 минут')
    expect(listCalls().at(-1)!.query.get('filter')).toBe('all')
    expect(window.sessionStorage.getItem('crm.chats.filter')).toBe('all')
  })

  it('порог берётся из настроек, а не из константы', async () => {
    counters = { ...COUNTERS, over_threshold_minutes: 45 }
    await open()
    expect(text()).toContain('2 клиента ждут больше 45 минут')
  })

  it('никто не ждёт долго — баннера нет', async () => {
    counters = { ...COUNTERS, over_threshold: 0 }
    await open()
    expect(text()).not.toContain('ждут больше')
  })
})

describe('«Взять первого в очереди»', () => {
  it('первое нажатие: без skip, открывает чат, запоминает его вместе со временем ожидания', async () => {
    server.on('GET', '/api/v1/conversations/next', { body: conversation(5, { awaiting_reply_since: T1 }) })
    await open()

    await click(takeButton())

    expect(nextCalls()).toHaveLength(1)
    expect(nextCalls()[0].query.has('skip')).toBe(false)
    expect(loc()).toBe('/chats/5')
    expect(JSON.parse(takenStored()!)).toEqual([{ id: 5, since: T1 }])
    // Взятых не было — лишнего запроса «кто ждёт» нет
    expect(lookupCalls()).toHaveLength(0)
  })

  it('повторное нажатие: взятый уходит в skip, и открывается следующий', async () => {
    server.on('GET', '/api/v1/conversations/next', (call) => ({
      body: call.query.get('skip') ? conversation(6, { awaiting_reply_since: T2 }) : conversation(5, { awaiting_reply_since: T1 }),
    }))
    // Чат 5 по-прежнему ждёт с того же момента — «уже открывал»
    server.on('GET', '/api/v1/conversations', (call) => ({
      body: {
        items: call.query.get('limit') === '100' ? [conversation(5, { awaiting_reply_since: T1 })] : [conversation(11)],
        next_cursor: null,
      },
    }))
    await open()

    await click(takeButton())
    await click(takeButton())

    expect(nextCalls().map((c) => c.query.get('skip'))).toEqual([null, '5'])
    expect(loc()).toBe('/chats/6')
    expect(JSON.parse(takenStored()!).map((e: { id: number }) => e.id)).toEqual([5, 6])
  })

  it('кто написал снова после взятия — не пропускается (убран из skip)', async () => {
    server.on('GET', '/api/v1/conversations/next', (call) => ({
      body: conversation(call.query.get('skip') ? 99 : 5, { awaiting_reply_since: T1 }),
    }))
    // Чат 5 ответили, а клиент написал снова — ожидание началось позже момента взятия
    server.on('GET', '/api/v1/conversations', (call) => ({
      body: {
        items: call.query.get('limit') === '100' ? [conversation(5, { awaiting_reply_since: T2 })] : [conversation(11)],
        next_cursor: null,
      },
    }))
    await open()

    await click(takeButton())
    await click(takeButton())

    expect(lookupCalls()).toHaveLength(1)
    expect(lookupCalls()[0].query.get('filter')).toBe('awaiting')
    // На втором нажатии skip пуст: чат 5 снова в очереди, а не «уже открывал»
    expect(nextCalls().map((c) => c.query.get('skip'))).toEqual([null, null])
  })

  it('не удалось спросить, кто ждёт, — кнопка всё равно работает со своим списком', async () => {
    server.on('GET', '/api/v1/conversations/next', (call) => ({
      body: conversation(call.query.get('skip') ? 6 : 5, { awaiting_reply_since: T1 }),
    }))
    server.on('GET', '/api/v1/conversations', (call) =>
      call.query.get('limit') === '100'
        ? errorReply(500, 'boom', 'Сервер недоступен')
        : { body: { items: [conversation(11)], next_cursor: null } },
    )
    await open()

    await click(takeButton())
    await click(takeButton())

    expect(nextCalls().map((c) => c.query.get('skip'))).toEqual([null, '5'])
    expect(loc()).toBe('/chats/6')
  })

  it('очередь пройдена, ждущие есть: честный текст и круг начинается заново', async () => {
    window.sessionStorage.setItem('crm.chats.taken.1', JSON.stringify([{ id: 5, since: T1 }]))
    server.on('GET', '/api/v1/conversations/next', errorReply(404, 'queue_done', 'queue_done'))
    server.on('GET', '/api/v1/conversations', (call) => ({
      body: {
        items: call.query.get('limit') === '100' ? [conversation(5, { awaiting_reply_since: T1 })] : [conversation(11)],
        next_cursor: null,
      },
    }))
    await open()

    await click(takeButton())

    expect(toasts()).toEqual(['Все ждущие чаты вы уже открывали. Нажмите ещё раз — начнём сначала'])
    expect(takenStored()).toBeNull()
    expect(loc()).toBe('/chats')
  })

  it('очередь пройдена, ждущих нет: другой текст — не «все ждущие»', async () => {
    counters = { ...COUNTERS, awaiting: 0, over_threshold: 0 }
    window.sessionStorage.setItem('crm.chats.taken.1', JSON.stringify([{ id: 5, since: null }]))
    server.on('GET', '/api/v1/conversations/next', errorReply(404, 'queue_done', 'queue_done'))
    await open()

    await click(takeButton())

    expect(toasts()).toEqual(['Больше нет чатов в очереди. Нажмите ещё раз — начнём сначала'])
    expect(takenStored()).toBeNull()
    // Никто не ждёт — спрашивать у сервера «кто ждёт» незачем
    expect(lookupCalls()).toHaveLength(0)
  })

  it('прочий 404 показывает текст сервера и взятых не трогает', async () => {
    window.sessionStorage.setItem('crm.chats.taken.1', JSON.stringify([{ id: 5, since: null }]))
    server.on('GET', '/api/v1/conversations/next', errorReply(404, 'not_found', 'Нет чатов в работе'))
    server.on('GET', '/api/v1/conversations', (call) => ({
      body: { items: call.query.get('limit') === '100' ? [] : [conversation(11)], next_cursor: null },
    }))
    await open()

    await click(takeButton())

    expect(toasts()).toEqual(['Нет чатов в работе'])
    expect(JSON.parse(takenStored()!)).toEqual([{ id: 5, since: null }])
  })

  it('сбой сервера — уведомление об ошибке, а не тишина', async () => {
    server.on('GET', '/api/v1/conversations/next', errorReply(500, 'boom', 'Сервер недоступен'))
    await open()

    await click(takeButton())

    expect(useToasts.getState().items.map((item) => [item.kind, item.text])).toEqual([['error', 'Сервер недоступен']])
  })

  it('чатов нет совсем — кнопка выключена, а не обманывает', async () => {
    counters = { ...COUNTERS, total: 0, awaiting: 0, over_threshold: 0 }
    await open()
    expect(takeButton()!.disabled).toBe(true)
  })
})

describe('«Показано N из M»', () => {
  it('в списке есть продолжение — подпись берёт M из счётчиков сервера', async () => {
    counters = { ...COUNTERS, total: 45 }
    server.on('GET', '/api/v1/conversations', {
      body: { items: [conversation(11), conversation(12)], next_cursor: 'abc' },
    })
    await open()
    expect(text()).toContain('Показано 2 из 45')
    expect(byText('button', 'Показать ещё')).not.toBeNull()
  })

  it('фильтр «Ждут ответа» считает по своему счётчику', async () => {
    window.sessionStorage.setItem('crm.chats.filter', 'awaiting')
    counters = { ...COUNTERS, awaiting: 7 }
    server.on('GET', '/api/v1/conversations', {
      body: { items: [conversation(11), conversation(12)], next_cursor: 'abc' },
    })
    await open()
    expect(text()).toContain('Показано 2 из 7')
  })

  it('при поиске числа нет: счётчики поиска не знают', async () => {
    server.on('GET', '/api/v1/conversations', {
      body: { items: [conversation(11)], next_cursor: 'abc' },
    })
    await open('/chats?q=Анна')
    expect(text()).not.toContain('Показано')
    expect(byText('button', 'Показать ещё')).not.toBeNull()
  })

  it('весь список уместился — подписи нет', async () => {
    await open()
    expect(text()).not.toContain('Показано')
  })
})

describe('текст поиска по чатам', () => {
  it('открываешь страницу с ?q= — поле заполнено и список уже отфильтрован', async () => {
    await open('/chats?q=Ксения')
    expect(searchBox().value).toBe('Ксения')
    expect(listCalls()[0].query.get('q')).toBe('Ксения')
  })

  it('набранное попадает в адрес (без новой записи в истории), пустое — убирает ?q=', async () => {
    await open()
    await typeInto(searchBox(), 'Анна')
    await advance(350)

    expect(loc()).toBe('/chats?q=Анна')
    expect(listCalls().at(-1)!.query.get('q')).toBe('Анна')

    await typeInto(searchBox(), '')
    await advance(350)
    expect(loc()).toBe('/chats')
  })

  it('открыли чат из списка — поиск остаётся в адресе и в поле, список слева не сбрасывается', async () => {
    await open('/chats?q=Анна')

    const row = document.querySelector<HTMLAnchorElement>('a[href^="/chats/11"]')
    expect(row).not.toBeNull()
    await click(row)

    // Раздел пересобран (другой маршрут), а поиск на месте
    expect(document.getElementById('chat-pane')?.textContent).toBe('Чат 11')
    expect(loc()).toBe('/chats/11?q=Анна')
    expect(searchBox().value).toBe('Анна')
    expect(listCalls().at(-1)!.query.get('q')).toBe('Анна')
  })

  it('«Назад» на телефоне возвращает к несброшенному списку', async () => {
    await open('/chats?q=Анна')
    await click(document.querySelector('a[href^="/chats/11"]'))
    expect(loc()).toBe('/chats/11?q=Анна')

    await click(document.getElementById('probe-back'))

    expect(loc()).toBe('/chats?q=Анна')
    expect(searchBox().value).toBe('Анна')
    expect(listCalls().at(-1)!.query.get('q')).toBe('Анна')
  })

  it('«Взять первого» тоже не теряет поиск', async () => {
    server.on('GET', '/api/v1/conversations/next', { body: conversation(5, { awaiting_reply_since: T1 }) })
    await open('/chats?q=Анна')

    await click(takeButton())

    expect(loc()).toBe('/chats/5?q=Анна')
    expect(searchBox().value).toBe('Анна')
  })

  it('переход в раздел «Чаты» без поиска очищает и поле: адрес главнее набранного', async () => {
    await open('/chats/11?q=Анна')
    expect(searchBox().value).toBe('Анна')

    await click(document.getElementById('probe-chats'))

    expect(loc()).toBe('/chats')
    expect(searchBox().value).toBe('')
    expect(listCalls().at(-1)!.query.has('q')).toBe(false)
  })

  it('другой чат по ссылке без поиска (уведомление, карточка клиента): поле идёт за адресом', async () => {
    await open('/chats/11?q=Анна')
    expect(searchBox().value).toBe('Анна')

    // Тот же экран, другой чат — компонент не пересобирается, поле обязано обновиться само
    await click(document.getElementById('probe-chat12'))

    expect(loc()).toBe('/chats/12')
    expect(searchBox().value).toBe('')
    expect(listCalls().at(-1)!.query.has('q')).toBe(false)
  })

  it('поле не затирается, пока человек печатает: набранное не отбрасывается записью в адрес', async () => {
    await open()
    await typeInto(searchBox(), 'Ан')
    await advance(350)
    expect(loc()).toBe('/chats?q=Ан')
    await typeInto(searchBox(), 'Анна')
    expect(searchBox().value).toBe('Анна')
    await advance(350)
    expect(loc()).toBe('/chats?q=Анна')
    expect(searchBox().value).toBe('Анна')
  })

  it('поиск без результата называет запрос и не прячет, как его очистить', async () => {
    server.on('GET', '/api/v1/conversations', { body: { items: [], next_cursor: null } })
    await open('/chats?q=Нет такого')
    expect(text()).toContain('По запросу «Нет такого» ничего не найдено')
  })
})
