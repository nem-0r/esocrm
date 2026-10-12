import { describe, expect, it } from 'vitest'

import {
  MAX_AMOUNT_KOPECKS,
  money,
  moneyIssueText,
  parseMoney,
  parseMoneyDetailed,
  plural,
  waitingLabel,
  durationLabel,
  initials,
} from './format'

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

  it('не «исправляет» ввод молча: минус, буквы и экспонента — не сумма', () => {
    expect(parseMoney('-100')).toBeNull()
    expect(parseMoney('−100')).toBeNull()
    expect(parseMoney('1e5')).toBeNull()
    expect(parseMoney('12abc')).toBeNull()
    expect(parseMoney('1.2.3')).toBeNull()
    expect(parseMoney('1,234.50')).toBeNull()
    expect(parseMoney('100,123')).toBeNull()
    expect(parseMoney('')).toBeNull()
    expect(parseMoney('  ')).toBeNull()
    expect(parseMoney(',')).toBeNull()
  })

  it('понимает привычные записи суммы', () => {
    expect(parseMoney('4 500')).toBe(450000)
    expect(parseMoney('4\u00a0500')).toBe(450000)
    expect(parseMoney('4500,5')).toBe(450050)
    expect(parseMoney('4500.50')).toBe(450050)
    expect(parseMoney('4500 ₽')).toBe(450000)
    expect(parseMoney('4500 руб.')).toBe(450000)
    expect(parseMoney('4500.000000')).toBe(450000) // так отдаёт сумму Робокасса
    expect(parseMoney('0,01')).toBe(1)
    expect(parseMoney('0,00')).toBeNull()
  })

  it('отсекает заведомо опечаточные суммы', () => {
    expect(parseMoney('99999999999999999999')).toBeNull()
    expect(parseMoney('100000000')).toBe(10_000_000_000)
    expect(parseMoney('100000000,01')).toBeNull()
    expect(parseMoney('10000000000')).toBeNull()
  })
})

describe('предел суммы', () => {
  it('равен серверному: 100 млн ₽ — backend/app/services/money.py, MAX_AMOUNT', () => {
    // Если меняете число здесь — меняйте и MAX_AMOUNT на сервере, и наоборот:
    // тот же номер закреплён в backend/tests/unit/test_money_limits.py.
    expect(MAX_AMOUNT_KOPECKS).toBe(10_000_000_000)
  })

  it('сумма ровно на пределе проходит, на копейку больше — нет', () => {
    expect(parseMoneyDetailed('100 000 000')).toEqual({ ok: true, kopecks: MAX_AMOUNT_KOPECKS })
    expect(parseMoneyDetailed('100 000 000,01')).toEqual({ ok: false, reason: 'too_large' })
  })
})

describe('причины отказа parseMoneyDetailed', () => {
  it.each([
    ['', 'empty'],
    ['   ', 'empty'],
    ['₽', 'empty'],
    ['абв', 'not_number'],
    ['-100', 'not_number'],
    ['1e5', 'not_number'],
    ['1.2.3', 'not_number'],
    [',', 'not_number'],
    ['10.555', 'too_precise'],
    ['100,123', 'too_precise'],
    ['0', 'zero'],
    ['0,00', 'zero'],
    ['0.000', 'zero'],
    ['100000000,01', 'too_large'],
    ['99999999999999999999', 'too_large'],
    ['9'.repeat(400), 'too_large'],
  ] as const)('«%s» → %s', (input, reason) => {
    expect(parseMoneyDetailed(input)).toEqual({ ok: false, reason })
  })

  it('«4500.000000» — те же 4500 (так отдаёт сумму Робокасса)', () => {
    expect(parseMoneyDetailed('4500.000000')).toEqual({ ok: true, kopecks: 450000 })
  })

  it('копейки считаются без ошибок плавающей точки', () => {
    // 1,15 · 100 в числах с плавающей точкой даёт 114,99999999999999.
    expect(parseMoneyDetailed('1,15')).toEqual({ ok: true, kopecks: 115 })
    expect(parseMoneyDetailed('4500,5')).toEqual({ ok: true, kopecks: 450050 })
    expect(parseMoneyDetailed('0,07')).toEqual({ ok: true, kopecks: 7 })
  })

  it('у каждой причины свой текст, и ни один не врёт про другую', () => {
    expect(moneyIssueText('empty')).toBe('Введите сумму')
    expect(moneyIssueText('too_precise')).toBe('Не больше двух знаков после запятой')
    expect(spaces(moneyIssueText('too_large'))).toBe('Сумма не больше 100 000 000 ₽')
    expect(moneyIssueText('zero')).toBe('Стоимость должна быть больше нуля')
    expect(moneyIssueText('not_number')).toMatch(/цифрами/)
    const texts = (['empty', 'not_number', 'too_precise', 'too_large', 'zero'] as const).map(
      moneyIssueText,
    )
    expect(new Set(texts).size).toBe(5)
  })

  it('parseMoney и parseMoneyDetailed не расходятся', () => {
    for (const input of ['4900', '4 900,50', '0', 'абв', '10.555', '']) {
      const detailed = parseMoneyDetailed(input)
      expect(parseMoney(input)).toBe(detailed.ok ? detailed.kopecks : null)
    }
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
