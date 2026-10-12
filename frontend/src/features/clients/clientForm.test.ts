import { describe, expect, it } from 'vitest'

import { buildClientPatch, validateClientForm, type ClientFormValues } from './clientForm'

const TODAY = '2026-10-11'

const initial: ClientFormValues = {
  display_name: 'Анна Петрова',
  phone: '+7 916 200-10-01',
  birth_date: '1990-05-17',
  birth_time: '14:30',
  birth_time_approx: 'none',
  birth_city: 'Москва',
}

const edit = (patch: Partial<ClientFormValues>): ClientFormValues => ({ ...initial, ...patch })

describe('проверка формы клиента', () => {
  it('без изменений ошибок нет', () => {
    expect(validateClientForm(initial, initial, TODAY)).toEqual({})
  })

  it('год 2999 — ошибка у даты рождения', () => {
    const problems = validateClientForm(edit({ birth_date: '2999-01-01' }), initial, TODAY)
    expect(Object.keys(problems)).toEqual(['birth_date'])
    expect(problems.birth_date).toContain('в будущем')
  })

  it('телефон «++--1234567» и «abc» — ошибка у телефона', () => {
    for (const phone of ['++--1234567', 'abc']) {
      const problems = validateClientForm(edit({ phone }), initial, TODAY)
      expect(Object.keys(problems), phone).toEqual(['phone'])
    }
  })

  it('имя длиннее 120 символов и город длиннее 120 — ошибки у своих полей', () => {
    const problems = validateClientForm(
      edit({ display_name: 'я'.repeat(121), birth_city: 'г'.repeat(121) }),
      initial,
      TODAY,
    )
    expect(Object.keys(problems).sort()).toEqual(['birth_city', 'display_name'])
  })

  it('пустые значения — не ошибка: они очищают поле', () => {
    const cleared = edit({ display_name: '   ', phone: '', birth_date: '', birth_city: '' })
    expect(validateClientForm(cleared, initial, TODAY)).toEqual({})
  })

  it('старое значение в прежнем формате не мешает править соседнее поле', () => {
    const legacy: ClientFormValues = { ...initial, phone: '(916) 200-10-01', birth_date: '2999-01-01' }
    // Тронули только город — ошибок по телефону и дате быть не должно
    expect(validateClientForm({ ...legacy, birth_city: 'Казань' }, legacy, TODAY)).toEqual({})
    // А если телефон тронули — проверяется
    expect(
      Object.keys(validateClientForm({ ...legacy, phone: '(916) 200-10-02' }, legacy, TODAY)),
    ).toEqual(['phone'])
  })
})

describe('тело PATCH клиента', () => {
  it('отправляются только изменённые поля, время рождения — как раньше всегда', () => {
    const patch = buildClientPatch(edit({ birth_city: '  Казань ' }), initial)
    expect(patch).toEqual({ birth_time: '14:30', birth_time_approx: null, birth_city: 'Казань' })
    expect('phone' in patch).toBe(false)
    expect('display_name' in patch).toBe(false)
    expect('birth_date' in patch).toBe(false)
  })

  it('очищенное поле уходит как null', () => {
    const patch = buildClientPatch(
      edit({ display_name: '   ', phone: '', birth_date: '', birth_city: '' }),
      initial,
    )
    expect(patch.display_name).toBeNull()
    expect(patch.phone).toBeNull()
    expect(patch.birth_date).toBeNull()
    expect(patch.birth_city).toBeNull()
  })

  it('телефон уходит схлопнутым, имя — без двойных пробелов', () => {
    const patch = buildClientPatch(
      edit({ phone: '  +7   916  200--10-09 ', display_name: '  Анна   Иванова ' }),
      initial,
    )
    expect(patch.phone).toBe('+7 916 200-10-09')
    expect(patch.display_name).toBe('Анна Иванова')
  })

  it('точное время вытесняет приблизительное', () => {
    const withTime = buildClientPatch(edit({ birth_time: '09:00', birth_time_approx: 'evening' }), initial)
    expect(withTime.birth_time_approx).toBeNull()
    const noTime = buildClientPatch(edit({ birth_time: '', birth_time_approx: 'evening' }), initial)
    expect(noTime.birth_time).toBeNull()
    expect(noTime.birth_time_approx).toBe('evening')
    const nothing = buildClientPatch(edit({ birth_time: '', birth_time_approx: 'none' }), initial)
    expect(nothing.birth_time_approx).toBeNull()
  })
})
