import { describe, expect, it } from 'vitest'

import { ApiError } from '@/shared/api/client'

import { MIN_PASSWORD_LENGTH, passwordProblem, resetFailure } from './resetPassword'

describe('пароль на странице «Новый пароль»', () => {
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
