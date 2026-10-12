import { describe, expect, it } from 'vitest'

import {
  MAX_PERIOD_DATE,
  MIN_PERIOD_DATE,
  addDays,
  presetRange,
  rangeError,
  rangeLabel,
  todayInZone,
} from './period'

describe('addDays', () => {
  it('двигает дату через границы месяца и года', () => {
    expect(addDays('2026-10-07', -6)).toBe('2026-10-01')
    expect(addDays('2026-10-01', -1)).toBe('2026-09-30')
    expect(addDays('2026-01-03', -5)).toBe('2025-12-29')
    expect(addDays('2025-12-31', 1)).toBe('2026-01-01')
  })

  it('знает про високосный год', () => {
    expect(addDays('2024-03-01', -1)).toBe('2024-02-29')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })
})

describe('пресеты периода', () => {
  const today = '2026-10-07'

  it('неделя — шесть дней назад по сегодня (как и было)', () => {
    expect(presetRange('week', today)).toEqual({ from: '2026-10-01', to: today })
  })

  it('квартал — 89 дней назад по сегодня (как и было)', () => {
    expect(presetRange('quarter', today)).toEqual({ from: '2026-07-10', to: today })
  })

  it('год — 364 дня назад по сегодня (как и было)', () => {
    expect(presetRange('year', today)).toEqual({ from: '2025-10-08', to: today })
  })

  it('месяц — календарный: с 1-го числа по сегодня (D-39)', () => {
    expect(presetRange('month', today)).toEqual({ from: '2026-10-01', to: today })
    expect(presetRange('month', '2026-10-31')).toEqual({ from: '2026-10-01', to: '2026-10-31' })
    expect(presetRange('month', '2026-03-15')).toEqual({ from: '2026-03-01', to: '2026-03-15' })
  })

  it('месяц в первый день месяца — один день, а не «последние 30»', () => {
    expect(presetRange('month', '2026-11-01')).toEqual({ from: '2026-11-01', to: '2026-11-01' })
  })

  it('месяц никогда не начинается не с 1-го числа', () => {
    for (let day = 1; day <= 28; day += 1) {
      const date = `2026-02-${String(day).padStart(2, '0')}`
      expect(presetRange('month', date).from).toBe('2026-02-01')
    }
  })
})

describe('«сегодня» в часовом поясе организации', () => {
  // 22:30 UTC 11 октября — в Москве (UTC+3) уже 12-е, в Нью-Йорке ещё 11-е.
  const now = new Date('2026-10-11T22:30:00Z')

  it('берёт дату по поясу организации, а не по поясу браузера', () => {
    expect(todayInZone('Europe/Moscow', now)).toBe('2026-10-12')
    expect(todayInZone('UTC', now)).toBe('2026-10-11')
    expect(todayInZone('America/New_York', now)).toBe('2026-10-11')
    expect(todayInZone('Asia/Vladivostok', now)).toBe('2026-10-12')
  })

  it('без настройки или с неизвестным поясом не падает: берёт пояс браузера', () => {
    expect(todayInZone(undefined, now)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(todayInZone(null, now)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(todayInZone('Не/Пояс', now)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })
})

describe('rangeError', () => {
  it('нормальный период и один день — без ошибки', () => {
    expect(rangeError('2026-10-01', '2026-10-07')).toBeNull()
    expect(rangeError('2026-10-07', '2026-10-07')).toBeNull()
  })

  it('пустая дата — просим указать, а не подставляем период по умолчанию', () => {
    expect(rangeError('', '2026-10-07')).toBe('Укажите обе даты периода')
    expect(rangeError('2026-10-01', '')).toBe('Укажите обе даты периода')
    expect(rangeError('', '')).toBe('Укажите обе даты периода')
  })

  it('«с» позже «по»', () => {
    expect(rangeError('2026-10-08', '2026-10-07')).toBe('Начало периода позже конца')
  })

  it('границы 1900–2100 — как на сервере', () => {
    expect(rangeError(MIN_PERIOD_DATE, '2026-01-01')).toBeNull()
    expect(rangeError('2026-01-01', MAX_PERIOD_DATE)).toBeNull()
    const message = 'Период должен лежать между 1900 и 2100 годами'
    expect(rangeError('1899-12-31', '2026-01-01')).toBe(message)
    expect(rangeError('2026-01-01', '2101-01-01')).toBe(message)
    // Год из одной цифры получается, пока человек печатает дату в поле.
    expect(rangeError('0002-10-05', '2026-10-07')).toBe(message)
  })

  it('несуществующая дата — не период', () => {
    expect(rangeError('2026-02-30', '2026-03-01')).toBe('Дата указана неверно')
    expect(rangeError('вчера', '2026-03-01')).toBe('Дата указана неверно')
  })
})

describe('rangeLabel', () => {
  it('год пишется один раз, в конце', () => {
    expect(rangeLabel({ from: '2026-10-01', to: '2026-10-07' })).toBe('1 октября — 7 октября 2026')
    expect(rangeLabel({ from: '2026-09-01', to: '2026-10-07' })).toBe('1 сентября — 7 октября 2026')
  })

  it('на стыке годов год виден у обеих дат', () => {
    expect(rangeLabel({ from: '2025-12-30', to: '2026-01-07' })).toBe(
      '30 декабря 2025 — 7 января 2026',
    )
  })

  it('один день — без тире', () => {
    expect(rangeLabel({ from: '2026-10-07', to: '2026-10-07' })).toBe('7 октября 2026')
  })
})
