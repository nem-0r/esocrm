/**
 * Чистая логика страницы «Новый пароль» (D-38): проверка ввода и разбор ошибок
 * сервера. Вынесена из компонента, чтобы проверяться тестами без браузера.
 */

import { ApiError } from '@/shared/api/client'

/** Политика пароля та же, что на сервере (приглашения, смена пароля в профиле). */
export const MIN_PASSWORD_LENGTH = 8

/** Что не так с введённым паролем, либо null. Совпадение проверяем только когда повтор начат. */
export function passwordProblem(password: string, repeat: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Пароль должен быть не короче ${MIN_PASSWORD_LENGTH} символов`
  }
  if (password !== repeat) return 'Пароли не совпадают'
  return null
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
