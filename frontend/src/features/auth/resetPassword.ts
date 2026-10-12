/**
 * Чистая логика страницы «Новый пароль» (D-38): проверка ввода и разбор ошибок
 * сервера. Вынесена из компонента, чтобы проверяться тестами без браузера.
 */

import { ApiError } from '@/shared/api/client'

/** Политика пароля та же, что на сервере (приглашения, смена пароля в профиле). */
export const MIN_PASSWORD_LENGTH = 8
/**
 * Сервер (bcrypt) учитывает не больше 72 байт пароля. Считаются байты UTF-8, а не
 * символы: русская буква — два байта, то есть до 36 русских букв.
 */
export const MAX_PASSWORD_BYTES = 72
export const PASSWORD_LENGTH_HINT = `Не короче ${MIN_PASSWORD_LENGTH} символов, не длиннее ${MAX_PASSWORD_BYTES} латинских (русская буква считается за две)`
export const PASSWORD_TOO_LONG = `Пароль слишком длинный: не больше ${MAX_PASSWORD_BYTES} латинских букв и цифр (русская буква считается за две)`

export function passwordBytes(password: string): number {
  return new TextEncoder().encode(password).length
}

/** Что не так с введённым паролем, либо null. Совпадение проверяем только когда повтор начат. */
export function passwordProblem(password: string, repeat: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Пароль должен быть не короче ${MIN_PASSWORD_LENGTH} символов`
  }
  if (passwordBytes(password) > MAX_PASSWORD_BYTES) return PASSWORD_TOO_LONG
  if (password !== repeat) return 'Пароли не совпадают'
  return null
}

/**
 * Токен ссылки сброса. Новая ссылка — `/reset#<токен>`: всё после «#» браузер не
 * отправляет на сервер, токен не оседает в журналах. Прежняя — `/reset/<токен>`
 * (выданные до обновления ссылки должны работать). Фрагмент важнее пути.
 */
export function tokenFromLocation(hash: string, pathToken?: string): string {
  const fromHash = hash.replace(/^#/, '').trim()
  return fromHash || (pathToken ?? '').trim()
}

/**
 * Ссылка мертва (неизвестна, просрочена, уже использована, выдана заново) — сервер
 * отвечает на все эти случаи одинаково, 404 `reset_link_invalid`; интерфейс тоже
 * не пытается их различать: человеку нужно одно — попросить новую ссылку.
 */
export type ResetFailure = 'invalid' | 'throttled' | 'other'

export function resetFailure(error: unknown): ResetFailure {
  if (error instanceof ApiError) {
    if (error.status === 404) return 'invalid'
    if (error.status === 429) return 'throttled'
  }
  return 'other'
}

export const RESET_LINK_INVALID_TITLE = 'Ссылка недействительна'
export const RESET_LINK_INVALID_HINT =
  'Она устарела, уже использована или выдана заново. Попросите руководителя выдать новую.'
