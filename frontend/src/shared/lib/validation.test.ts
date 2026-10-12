import { describe, expect, it } from 'vitest'

import {
  BIRTH_DATE_MIN,
  NAME_MAX_LENGTH,
  PHONE_MAX_LENGTH,
  birthDateError,
  normalizePhone,
  phoneError,
  textError,
  todayIso,
} from './validation'

describe('телефон', () => {
  it('нормальные номера проходят', () => {
    for (const value of [
      '+7 916 200-10-01',
      '+79162001001',
      '8 916 200 10 01',
      '916-200-10-01',
      '1234567',
      '123456789012345',
    ]) {
      expect(phoneError(value), value).toBeNull()
    }
  })

  it('пустой телефон — не ошибка, поле необязательное', () => {
    expect(phoneError('')).toBeNull()
    expect(phoneError('   ')).toBeNull()
  })

  it('«++--1234567» и прочий мусор отклоняются', () => {
    for (const value of [
      '++--1234567',
      '+-1234567',
      '--1234567',
      '++79162001001',
      '+7 916 +200 10 01',
      '1234567-',
      '+',
      'abc',
      '123456',
      '1234567890123456',
      '+7 (916) 200-10-01',
    ]) {
      expect(phoneError(value), value).not.toBeNull()
    }
  })

  it('повторные пробелы и дефисы схлопываются, как на сервере', () => {
    expect(normalizePhone('  +7   916  200--10-01 ')).toBe('+7 916 200-10-01')
    expect(phoneError('  +7   916  200--10-01 ')).toBeNull()
  })

  it('длиннее колонки — отдельное сообщение', () => {
    const long = '1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7'
    expect(long.length).toBeGreaterThan(PHONE_MAX_LENGTH)
    expect(phoneError(long)).toContain('не длиннее 32')
  })

  it('число цифр: меньше 7 и больше 15 — ошибка', () => {
    expect(phoneError('+7 916')).toContain('от 7 до 15')
    expect(phoneError('+7 916 200 10 01 22 33 44')).toContain('от 7 до 15')
  })
})

describe('дата рождения', () => {
  const today = '2026-10-11'

  it('пусто — не ошибка', () => {
    expect(birthDateError('', today)).toBeNull()
  })

  it('границы: 1900-01-01 и сегодня проходят', () => {
    expect(birthDateError(BIRTH_DATE_MIN, today)).toBeNull()
    expect(birthDateError(today, today)).toBeNull()
    expect(birthDateError('1990-05-17', today)).toBeNull()
  })

  it('год 2999 и завтрашний день — «в будущем»', () => {
    expect(birthDateError('2999-01-01', today)).toContain('в будущем')
    expect(birthDateError('2026-10-12', today)).toContain('в будущем')
  })

  it('до 1900 — отдельная причина', () => {
    expect(birthDateError('1899-12-31', today)).toContain('1900')
  })

  it('несуществующая дата и шестизначный год, который умеет вводить браузер', () => {
    expect(birthDateError('2000-02-30', today)).toContain('неверно')
    expect(birthDateError('2000-13-01', today)).toContain('неверно')
    expect(birthDateError('275760-01-01', today)).toContain('неверно')
    expect(birthDateError('31.12.1990', today)).toContain('неверно')
  })

  it('29 февраля — только в високосный год', () => {
    expect(birthDateError('2000-02-29', today)).toBeNull()
    expect(birthDateError('2001-02-29', today)).toContain('неверно')
  })

  it('сегодня берётся по локальным часам устройства', () => {
    expect(todayIso(new Date(2026, 9, 11, 23, 59))).toBe('2026-10-11')
    expect(todayIso(new Date(2026, 0, 5))).toBe('2026-01-05')
  })
})

describe('короткий текст', () => {
  it('длина считается после схлопывания пробелов', () => {
    expect(textError('я'.repeat(NAME_MAX_LENGTH), NAME_MAX_LENGTH, 'Имя')).toBeNull()
    expect(textError('я'.repeat(NAME_MAX_LENGTH + 1), NAME_MAX_LENGTH, 'Имя')).toContain(
      'не длиннее 120',
    )
    expect(textError(`  ${'я'.repeat(NAME_MAX_LENGTH)}  `, NAME_MAX_LENGTH, 'Имя')).toBeNull()
  })

  it('пустое и из одних пробелов — не ошибка (сервер превратит в «нет значения»)', () => {
    expect(textError('', NAME_MAX_LENGTH, 'Имя')).toBeNull()
    expect(textError('    ', NAME_MAX_LENGTH, 'Имя')).toBeNull()
  })

  it('управляющие символы отклоняются', () => {
    expect(textError('Иван\u0000Петров', NAME_MAX_LENGTH, 'Имя')).toContain('недопустимые')
  })

  it('разметка в имени — обычный текст', () => {
    expect(textError('<script>alert(1)</script>', NAME_MAX_LENGTH, 'Имя')).toBeNull()
  })
})
