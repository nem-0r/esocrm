import { describe, expect, it } from 'vitest'

import { hasDealDraft, newItemDraft, normalizeServiceName } from './lib'

describe('normalizeServiceName', () => {
  it('не различает «е» и «ё»', () => {
    expect(normalizeServiceName('Расчёт натальной карты')).toBe(
      normalizeServiceName('расчет натальной карты'),
    )
  })

  it('не различает регистр и пробелы по краям', () => {
    expect(normalizeServiceName('  Натальная Карта  ')).toBe(normalizeServiceName('натальная карта'))
  })

  it('не считает разные названия одинаковыми', () => {
    expect(normalizeServiceName('Таро расклад')).not.toBe(normalizeServiceName('Натальная карта'))
  })
})

describe('hasDealDraft', () => {
  it('пустой черновик — терять нечего', () => {
    expect(hasDealDraft([newItemDraft()])).toBe(false)
  })

  it('название услуги без суммы — уже есть что терять', () => {
    expect(hasDealDraft([{ ...newItemDraft(), name: 'Таро' }])).toBe(true)
  })

  it('только сумма без названия — тоже считается', () => {
    expect(hasDealDraft([{ ...newItemDraft(), amount: '4900' }])).toBe(true)
  })

  it('текст реквизитов или сопроводительного письма учитывается отдельно', () => {
    expect(hasDealDraft([newItemDraft()], ['', 'Здравствуйте!'])).toBe(true)
    expect(hasDealDraft([newItemDraft()], ['', '   '])).toBe(false)
  })
})
