import { describe, expect, it } from 'vitest'

import type { SearchResults } from '@/entities/types'

import {
  FULL_LIMIT,
  PREVIEW_LIMIT,
  clientHint,
  groupTotal,
  isTruncated,
  searchLimit,
  showAllLabel,
  totalLabel,
  canShowAll,
} from './lib'

function results(partial: Partial<SearchResults>): SearchResults {
  return { clients: [], deals: [], chats: [], files: [], managers: [], ...partial }
}

function clients(count: number): SearchResults['clients'] {
  return Array.from({ length: count }, (_, index) => ({
    id: index + 1,
    name: `Клиент ${index + 1}`,
    phone: null,
    tg_username: null,
    paid_amount: 0,
  }))
}

describe('clientHint', () => {
  it('показывает @username и телефон, а не служебный id', () => {
    expect(clientHint({ id: 703, tg_username: 'ksenia', phone: '+7 958 142-61-76' })).toBe(
      '@ksenia · +7 958 142-61-76',
    )
  })

  it('только @username, если телефона нет', () => {
    expect(clientHint({ id: 1, tg_username: 'ksenia', phone: null })).toBe('@ksenia')
  })

  it('только телефон, если @username нет', () => {
    expect(clientHint({ id: 1, tg_username: null, phone: '+7 958 142-61-76' })).toBe(
      '+7 958 142-61-76',
    )
  })

  it('id — только если больше показать нечего', () => {
    expect(clientHint({ id: 703, tg_username: null, phone: null })).toBe('id 703')
    expect(clientHint({ id: 703, phone: '' })).toBe('id 703')
  })

  it('старый сервер не присылает tg_username — это не ошибка', () => {
    expect(clientHint({ id: 5, phone: '+7 900 000-00-00' })).toBe('+7 900 000-00-00')
  })
})

describe('searchLimit', () => {
  it('без фильтра и с несколькими — короткий предпросмотр по группам', () => {
    expect(searchLimit([])).toBe(PREVIEW_LIMIT)
    expect(searchLimit(['clients', 'deals'])).toBe(PREVIEW_LIMIT)
  })

  it('одна вкладка — полный список до предела', () => {
    expect(searchLimit(['clients'])).toBe(FULL_LIMIT)
  })
})

describe('groupTotal', () => {
  it('берёт общее число из totals', () => {
    expect(groupTotal(results({ clients: clients(5), totals: { clients: 17 } }), 'clients')).toBe(
      17,
    )
  })

  it('без totals (старый сервер) — сколько пришло', () => {
    expect(groupTotal(results({ clients: clients(5) }), 'clients')).toBe(5)
  })

  it('totals не может быть меньше показанного', () => {
    expect(groupTotal(results({ clients: clients(5), totals: { clients: 2 } }), 'clients')).toBe(5)
  })

  it('пустая группа — ноль', () => {
    expect(groupTotal(results({}), 'files')).toBe(0)
  })
})

describe('totalLabel', () => {
  it('точное число до предела', () => {
    expect(totalLabel(7)).toBe('7')
    expect(totalLabel(FULL_LIMIT)).toBe('50')
  })

  it('больше предела — «50+», а не выдуманное число', () => {
    expect(totalLabel(FULL_LIMIT + 1)).toBe('50+')
  })
})

describe('showAllLabel / canShowAll', () => {
  const found = results({ clients: clients(5), totals: { clients: 17 } })

  it('кнопка есть, когда найдено больше, чем показано', () => {
    expect(canShowAll(found, 'clients', [])).toBe(true)
    expect(showAllLabel(groupTotal(found, 'clients'))).toBe('Показать все (17)')
  })

  it('кнопки нет, когда показано всё', () => {
    expect(
      canShowAll(results({ clients: clients(3), totals: { clients: 3 } }), 'clients', []),
    ).toBe(false)
  })

  it('кнопки нет внутри вкладки: там уже показан полный список', () => {
    const full = results({ clients: clients(50), totals: { clients: 51 } })
    expect(canShowAll(full, 'clients', ['clients'])).toBe(false)
  })

  it('больше 50 — подпись «50+»', () => {
    expect(showAllLabel(51)).toBe('Показать все (50+)')
  })
})

describe('isTruncated', () => {
  it('в полном списке при найденном больше 50 — предупреждаем', () => {
    const full = results({ clients: clients(50), totals: { clients: 51 } })
    expect(isTruncated(full, 'clients', ['clients'])).toBe(true)
  })

  it('в предпросмотре предупреждение не нужно — там есть кнопка', () => {
    const preview = results({ clients: clients(5), totals: { clients: 51 } })
    expect(isTruncated(preview, 'clients', [])).toBe(false)
  })

  it('когда показано всё, предупреждения нет', () => {
    const full = results({ clients: clients(7), totals: { clients: 7 } })
    expect(isTruncated(full, 'clients', ['clients'])).toBe(false)
  })
})
