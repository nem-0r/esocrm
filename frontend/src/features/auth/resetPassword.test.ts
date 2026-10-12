import { describe, expect, it } from 'vitest'

import { ApiError } from '@/shared/api/client'

import {
  MAX_PASSWORD_BYTES,
  MIN_PASSWORD_LENGTH,
  passwordBytes,
  passwordProblem,
  resetFailure,
  tokenFromLocation,
} from './resetPassword'

// Русская парольная фраза: 47 символов, но 88 байт — больше предела bcrypt (72 байта).
const LONG_RU = 'Очень длинная парольная фраза для проверки сети'

describe('пароль на странице «Новый пароль»', () => {
  it('длина считается в байтах UTF-8, как на сервере', () => {
    expect(MAX_PASSWORD_BYTES).toBe(72)
    expect(LONG_RU.length).toBeLessThan(72)
    expect(passwordBytes(LONG_RU)).toBe(88)
    expect(passwordBytes('я')).toBe(2)
  })

  it('длиннее 72 байт — «слишком длинный», хотя символов меньше 72', () => {
    expect(passwordProblem(LONG_RU, LONG_RU)).toContain('Пароль слишком длинный')
    expect(passwordProblem('a'.repeat(73), 'a'.repeat(73))).toContain('слишком длинный')
    expect(passwordProblem('я'.repeat(37), 'я'.repeat(37))).toContain('слишком длинный')
  })

  it('ровно 72 байта — можно', () => {
    expect(passwordProblem('a'.repeat(72), 'a'.repeat(72))).toBeNull()
    expect(passwordProblem('я'.repeat(36), 'я'.repeat(36))).toBeNull()
  })

  it('короче восьми символов — ошибка с числом', () => {
    expect(passwordProblem('1234567', '1234567')).toContain(String(MIN_PASSWORD_LENGTH))
  })

  it('разные пароли — «не совпадают»', () => {
    expect(passwordProblem('Qa-second-5678', 'Qa-second-5679')).toBe('Пароли не совпадают')
    expect(passwordProblem('Qa-second-5678', '')).toBe('Пароли не совпадают')
  })

  it('ровно восемь и совпадают — можно', () => {
    expect(passwordProblem('12345678', '12345678')).toBeNull()
  })

  it('длина проверяется раньше совпадения: одно сообщение за раз', () => {
    expect(passwordProblem('abc', 'abd')).toContain('не короче')
  })
})

describe('токен из адреса', () => {
  it('новая ссылка /reset#<токен>: токен из фрагмента', () => {
    expect(tokenFromLocation('#abc_DEF-123')).toBe('abc_DEF-123')
    expect(tokenFromLocation('#  abc  ')).toBe('abc')
  })

  it('прежняя ссылка /reset/<токен>: токен из пути', () => {
    expect(tokenFromLocation('', 'abc_DEF-123')).toBe('abc_DEF-123')
  })

  it('фрагмент важнее пути; без токена — пустая строка', () => {
    expect(tokenFromLocation('#fromHash', 'fromPath')).toBe('fromHash')
    expect(tokenFromLocation('')).toBe('')
    expect(tokenFromLocation('#')).toBe('')
    expect(tokenFromLocation('', undefined)).toBe('')
  })
})

describe('разбор ответа сервера', () => {
  it('404 — ссылка мертва, что бы ни было причиной', () => {
    const dead = new ApiError(404, 'reset_link_invalid', 'Ссылка недействительна или истекла')
    expect(resetFailure(dead)).toBe('invalid')
  })

  it('429 — слишком много неверных попыток', () => {
    expect(resetFailure(new ApiError(429, 'rate_limited', 'Слишком много попыток'))).toBe('throttled')
  })

  it('остальное (нет сети, 500, 422 по паролю) — не про ссылку', () => {
    expect(resetFailure(new ApiError(0, 'network', 'Нет связи'))).toBe('other')
    expect(resetFailure(new ApiError(500, 'error', 'Сервер недоступен'))).toBe('other')
    expect(resetFailure(new ApiError(422, 'validation', 'Пароль слишком короткий'))).toBe('other')
    expect(resetFailure(new Error('что-то'))).toBe('other')
    expect(resetFailure(undefined)).toBe('other')
  })
})
