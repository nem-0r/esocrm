import { describe, expect, it } from 'vitest'

import { ApiError } from '@/shared/api/client'

import { fieldErrors } from './lib'

describe('ошибки по полям', () => {
  it('422 проверки данных: поле названо в details.field, текст — в сообщении', () => {
    const error = new ApiError(422, 'validation', 'Проверьте формат телефона', {
      field: 'phone',
      errors: [{ field: 'phone', type: 'value_error', message: 'Value error, ...' }],
    })
    expect(fieldErrors(error)).toEqual({ phone: 'Проверьте формат телефона' })
  })

  it('ошибка сервиса с полем (Invalid(..., field=...)) читается так же', () => {
    const error = new ApiError(422, 'invalid', 'Дата рождения не может быть в будущем', {
      field: 'birth_date',
    })
    expect(fieldErrors(error)).toEqual({ birth_date: 'Дата рождения не может быть в будущем' })
  })

  it('прежний формат details.fields не сломан', () => {
    const error = new ApiError(422, 'invalid', 'Проверьте поля', {
      fields: { full_name: 'Укажите имя', ignored: 5 },
    })
    expect(fieldErrors(error)).toEqual({ full_name: 'Укажите имя' })
  })

  it('ошибка без привязки к полю даёт пустой набор — её покажет общая строка', () => {
    expect(fieldErrors(new ApiError(409, 'conflict', 'Сотрудник с такой почтой уже заведён'))).toEqual(
      {},
    )
    expect(fieldErrors(new Error('сеть'))).toEqual({})
    expect(fieldErrors(null)).toEqual({})
  })
})
