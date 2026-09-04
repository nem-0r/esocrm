import { describe, expect, it } from 'vitest'

import { money, parseMoney, plural, waitingLabel, durationLabel, initials } from './format'

/**
 * Intl разделяет разряды неразрывным пробелом — так «4 900 ₽» не переносится
 * по строкам. Проверяем формат числа, а не конкретный код пробела: иначе тест
 * ломается от смены версии Node, хотя пользователь видит то же самое.
 */
const spaces = (value: string) => value.replace(/[\s\u00a0\u202f]/g, ' ')

describe('деньги', () => {
  it('копейки превращаются в рубли с разрядами', () => {
    expect(spaces(money(490000))).toBe('4 900 ₽')
    expect(spaces(money(100000000))).toBe('1 000 000 ₽')
    expect(spaces(money(0))).toBe('0 ₽')
  })

  it('копейки показываются, только если они есть', () => {
    expect(spaces(money(490050))).toBe('4 900,50 ₽')
    expect(spaces(money(490005))).toBe('4 900,05 ₽')
  })

  it('пустое значение не притворяется нулём', () => {
    expect(money(null)).toBe('—')
    expect(money(undefined)).toBe('—')
  })

  it('ввод рублей превращается в целые копейки', () => {
    expect(parseMoney('4900')).toBe(490000)
    expect(parseMoney('4 900,50')).toBe(490050)
    expect(parseMoney('0')).toBeNull()
    expect(parseMoney('абв')).toBeNull()
  })
})

describe('склонения', () => {
  it('считает по русским правилам', () => {
    expect(plural(1, 'сделка', 'сделки', 'сделок')).toBe('сделка')
    expect(plural(2, 'сделка', 'сделки', 'сделок')).toBe('сделки')
    expect(plural(5, 'сделка', 'сделки', 'сделок')).toBe('сделок')
    expect(plural(11, 'сделка', 'сделки', 'сделок')).toBe('сделок')
    expect(plural(21, 'сделка', 'сделки', 'сделок')).toBe('сделка')
  })
})

describe('время ожидания', () => {
  it('единица измерения подписана всегда', () => {
    expect(waitingLabel(42)).toBe('42 мин')
    expect(waitingLabel(60)).toBe('1 ч')
    expect(waitingLabel(190)).toBe('3 ч 10 мин')
    expect(waitingLabel(2880)).toBe('2 дня')
    expect(waitingLabel(null)).toBe('')
  })

  it('секунды округляются до минут', () => {
    expect(durationLabel(720)).toBe('12 мин')
    expect(durationLabel(20)).toBe('<1 мин')
    expect(durationLabel(null)).toBe('—')
  })
})

describe('инициалы', () => {
  it('берёт первые буквы имени и фамилии', () => {
    expect(initials('Мария Кузнецова')).toBe('МК')
    expect(initials('Игорь')).toBe('ИГ')
    expect(initials('')).toBe('?')
  })
})
