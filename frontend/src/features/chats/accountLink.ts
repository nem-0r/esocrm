/**
 * Связь аккаунта с Telegram в чате (D-37).
 *
 * Правило то же, что на сервере (backend/app/services/account_link.py): отправка
 * закрыта при `error`, `disconnected` и `pending`. Интерфейс и сервер обязаны
 * совпадать — иначе поле ввода будет открыто там, где сервер ответит отказом.
 */

import type { AccountStatus } from '@/entities/types'

export function hasNoLink(status: AccountStatus | null | undefined): boolean {
  return status === 'error' || status === 'disconnected' || status === 'pending'
}

/** Текст баннера над полем ввода. Руководителю не предлагаем сообщить самому себе. */
export function offlineBannerText(accountTitle: string, isAdmin: boolean): string {
  const base = `Аккаунт «${accountTitle}» отключён — сообщения не уходят.`
  return isAdmin ? base : `${base} Сообщите руководителю`
}

/** Пояснение в самом поле ввода, пока оно выключено. Короткое: на телефоне поле узкое. */
export const OFFLINE_PLACEHOLDER = 'Аккаунт отключён'
