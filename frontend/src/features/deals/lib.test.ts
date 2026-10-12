import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  activeItems,
  amountFieldError,
  amountLiveError,
  canRemoveItem,
  draftTotal,
  expiryLabel,
  hasDealDraft,
  itemsError,
  newItemDraft,
  normalizeServiceName,
  removeItem,
  restoreItem,
  toItemsPayload,
  type ItemDraft,
} from './lib'

/** Неразрывные пробелы в «100 000 000 ₽» — сравниваем по виду, а не по коду пробела. */
const spaces = (value: string | null) => (value ?? '').replace(/[\s\u00a0\u202f]/g, ' ')

function item(name: string, amount: string, extra: Partial<ItemDraft> = {}): ItemDraft {
  return { ...newItemDraft(), name, amount, ...extra }
}

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

  it('убранная услуга с введённым текстом всё ещё черновик: её можно вернуть', () => {
    expect(hasDealDraft([newItemDraft(), item('Таро', '4900', { removed: true })])).toBe(true)
  })

  it('убранная пустая строка черновика не создаёт', () => {
    expect(hasDealDraft([newItemDraft(), { ...newItemDraft(), removed: true }])).toBe(false)
  })
})

describe('expiryLabel (BUG-03: «истекла» только у истёкших)', () => {
  const now = new Date('2026-10-07T12:00:00Z').getTime()
  const at = (hours: number) => new Date(now + hours * 3_600_000).toISOString()

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
  })
  afterEach(() => vi.useRealTimers())

  it('ждёт оплаты: сколько осталось, в днях', () => {
    expect(expiryLabel(at(72), 'awaiting')).toBe('истекает через 3 дня')
    expect(expiryLabel(at(24), 'awaiting')).toBe('истекает через 1 день')
    expect(expiryLabel(at(24 * 5 + 1), 'awaiting')).toBe('истекает через 6 дней')
  })

  it('ждёт оплаты, меньше суток: в часах', () => {
    expect(expiryLabel(at(5), 'awaiting')).toBe('истекает через 5 часов')
    expect(expiryLabel(at(1), 'awaiting')).toBe('истекает через 1 час')
    expect(expiryLabel(at(2), 'awaiting')).toBe('истекает через 2 часа')
  })

  it('ждёт оплаты, меньше часа', () => {
    expect(expiryLabel(at(0.5), 'awaiting')).toBe('истекает менее чем через час')
  })

  it('ждёт оплаты, но срок вышел, а планировщик ещё не отметил, — «истекла»', () => {
    expect(expiryLabel(at(-1), 'awaiting')).toBe('истекла')
    expect(expiryLabel(at(0), 'awaiting')).toBe('истекла')
  })

  it('истекла по статусу — «истекла»', () => {
    expect(expiryLabel(at(-48), 'expired')).toBe('истекла')
  })

  it('оплачена и отменена: срок не показываем, хотя дата в сделке осталась', () => {
    expect(expiryLabel(at(-48), 'paid')).toBeNull()
    expect(expiryLabel(at(-48), 'cancelled')).toBeNull()
    expect(expiryLabel(at(72), 'paid')).toBeNull()
    expect(expiryLabel(at(72), 'cancelled')).toBeNull()
  })

  it('черновик и сделка без срока: ничего', () => {
    expect(expiryLabel(null, 'draft')).toBeNull()
    expect(expiryLabel(at(72), 'draft')).toBeNull()
    expect(expiryLabel(null, 'awaiting')).toBeNull()
    expect(expiryLabel(undefined, 'awaiting')).toBeNull()
    expect(expiryLabel('не дата', 'awaiting')).toBeNull()
  })
})

describe('поле суммы: у каждой ошибки своя причина (L3)', () => {
  it('нормальная сумма — без ошибки', () => {
    expect(amountFieldError('4900')).toBeNull()
    expect(amountFieldError('4 900,50')).toBeNull()
    expect(amountFieldError('100 000 000')).toBeNull()
  })

  it('пусто', () => {
    expect(amountFieldError('')).toBe('Введите сумму')
  })

  it('больше двух знаков после запятой', () => {
    expect(amountFieldError('10.555')).toBe('Не больше двух знаков после запятой')
  })

  it('слишком велика — предел как на сервере', () => {
    expect(spaces(amountFieldError('100000000,01'))).toBe('Сумма не больше 100 000 000 ₽')
    expect(spaces(amountFieldError('5000000000'))).toBe('Сумма не больше 100 000 000 ₽')
  })

  it('ноль', () => {
    expect(amountFieldError('0')).toBe('Стоимость должна быть больше нуля')
    expect(amountFieldError('0,00')).toBe('Стоимость должна быть больше нуля')
  })

  it('не число', () => {
    expect(amountFieldError('абв')).toMatch(/цифрами/)
    expect(amountFieldError('-100')).toMatch(/цифрами/)
  })
})

describe('amountLiveError: сумма в процессе набора', () => {
  it('завершающая запятая или точка, пока человек в поле, — ещё не ошибка', () => {
    expect(amountLiveError('4500,', true)).toBeNull()
    expect(amountLiveError('4500.', true)).toBeNull()
    expect(amountLiveError('4 500,', true)).toBeNull()
    expect(amountLiveError(' 4500, ', true)).toBeNull()
    // Неразрывный пробел (так форматируется «4 500») — тоже пробел.
    expect(amountLiveError('4\u00a0500,', true)).toBeNull()
  })

  it('поле покинули — та же запись снова ошибка: сервер её не примет', () => {
    expect(amountLiveError('4500,', false)).toMatch(/цифрами/)
    expect(amountLiveError('4500.', false)).toMatch(/цифрами/)
  })

  it('только запятая без цифр, две запятые и мусор — ошибка и во время набора', () => {
    expect(amountLiveError(',', true)).toMatch(/цифрами/)
    expect(amountLiveError('4500,,', true)).toMatch(/цифрами/)
    expect(amountLiveError('4,5,', true)).toMatch(/цифрами/)
    expect(amountLiveError('абв,', true)).toMatch(/цифрами/)
  })

  it('остальные причины не смягчаются', () => {
    expect(amountLiveError('10.555', true)).toBe('Не больше двух знаков после запятой')
    expect(amountLiveError('0', true)).toBe('Стоимость должна быть больше нуля')
    expect(spaces(amountLiveError('100000000,01', true))).toBe('Сумма не больше 100 000 000 ₽')
    expect(amountLiveError('', true)).toBe('Введите сумму')
  })

  it('верная сумма — без ошибки в обоих режимах', () => {
    expect(amountLiveError('4500,50', true)).toBeNull()
    expect(amountLiveError('4500,50', false)).toBeNull()
  })

  it('отправку формы не смягчает: «4500,» по-прежнему не проходит itemsError', () => {
    expect(itemsError([item('Таро', '4500,')])).toMatch(/цифрами/)
  })
})

describe('itemsError', () => {
  it('без услуг', () => {
    expect(itemsError([])).toBe('Добавьте хотя бы одну услугу')
  })

  it('нет названия', () => {
    expect(itemsError([item('', '100')])).toBe('Укажите название каждой услуги')
    expect(itemsError([item('   ', '100')])).toBe('Укажите название каждой услуги')
  })

  it('название длиннее 255 символов', () => {
    expect(itemsError([item('я'.repeat(256), '100')])).toBe(
      'Название услуги — не длиннее 255 символов',
    )
    expect(itemsError([item('я'.repeat(255), '100')])).toBeNull()
  })

  it('верный состав — ошибки нет', () => {
    expect(itemsError([item('Таро', '4900'), item('Натальная карта', '3 500,50')])).toBeNull()
  })

  it('одна услуга: причина без имени услуги', () => {
    expect(itemsError([item('Таро', '')])).toBe('Введите сумму')
    expect(itemsError([item('Таро', '10.555')])).toBe('Не больше двух знаков после запятой')
    expect(itemsError([item('Таро', '0')])).toBe('Стоимость должна быть больше нуля')
  })

  it('несколько услуг: ошибка называет, в какой именно', () => {
    expect(itemsError([item('Таро', '4900'), item('Натальная карта', '10.555')])).toBe(
      '«Натальная карта»: не больше двух знаков после запятой',
    )
  })

  it('ничего не говорит про «больше нуля», когда дело в лишних знаках', () => {
    expect(itemsError([item('Таро', '10.555')])).not.toMatch(/нуля/)
  })
})

describe('убрать / вернуть услугу (BUG-07)', () => {
  const tarot = item('Таро', '4900')
  const natal = item('Натальная карта', '3500')
  const synastry = item('Синастрия', '2000')

  it('убранная услуга остаётся на своём месте, помеченная removed', () => {
    const result = removeItem([tarot, natal, synastry], natal.key)
    expect(result.map((i) => i.key)).toEqual([tarot.key, natal.key, synastry.key])
    expect(result.map((i) => i.removed)).toEqual([false, true, false])
    expect(result[1].name).toBe('Натальная карта')
    expect(result[1].amount).toBe('3500')
  })

  it('вернуть: строка снова активна с теми же данными', () => {
    const removed = removeItem([tarot, natal], natal.key)
    const back = restoreItem(removed, natal.key)
    expect(back.map((i) => i.removed)).toEqual([false, false])
    expect(back[1]).toEqual(natal)
  })

  it('убранная строка не входит ни в итог, ни в отправляемый состав', () => {
    const items = removeItem([tarot, natal, synastry], natal.key)
    expect(draftTotal(items)).toBe(490000 + 200000)
    expect(toItemsPayload(items)).toEqual([
      { name: 'Таро', amount: 490000, service_id: null },
      { name: 'Синастрия', amount: 200000, service_id: null },
    ])
    expect(activeItems(items)).toHaveLength(2)
  })

  it('убранная строка с неверной суммой не мешает отправить', () => {
    const broken = item('Мусор', '10.555')
    expect(itemsError([tarot, broken])).not.toBeNull()
    expect(itemsError(removeItem([tarot, broken], broken.key))).toBeNull()
  })

  it('убранная строка без названия тоже не мешает', () => {
    const empty = newItemDraft()
    expect(itemsError(removeItem([tarot, empty], empty.key))).toBeNull()
  })

  it('«Убрать» доступно, пока активных строк больше одной', () => {
    expect(canRemoveItem([tarot])).toBe(false)
    expect(canRemoveItem([tarot, natal])).toBe(true)
    // Одна активная и одна убранная: последнюю активную убрать нельзя.
    expect(canRemoveItem(removeItem([tarot, natal], natal.key))).toBe(false)
    // Вернули — снова можно.
    expect(canRemoveItem(restoreItem(removeItem([tarot, natal], natal.key), natal.key))).toBe(true)
  })

  it('последнюю активную строку removeItem не убирает', () => {
    const only = removeItem([tarot], tarot.key)
    expect(only[0].removed).toBe(false)
    const oneLeft = removeItem(removeItem([tarot, natal], natal.key), tarot.key)
    expect(oneLeft.map((i) => i.removed)).toEqual([false, true])
  })

  it('неизвестный ключ ничего не меняет', () => {
    expect(removeItem([tarot, natal], 'нет-такого')).toEqual([tarot, natal])
    expect(restoreItem([tarot, natal], 'нет-такого')).toEqual([tarot, natal])
  })

  it('если все строки убраны напрямую, состав пуст и об этом сказано', () => {
    expect(itemsError([{ ...tarot, removed: true }])).toBe('Добавьте хотя бы одну услугу')
  })
})
