/**
 * Связь аккаунта с Telegram в чате (D-37).
 *
 * Правило то же, что на сервере (backend/app/services/account_link.py): отправка
 * закрыта при `error`, `disconnected` и `pending`. Интерфейс и сервер обязаны
 * совпадать — иначе поле ввода будет открыто там, где сервер ответит отказом.
 */

import type { AccountStatus } from '@/entities/types'
import { ApiError } from '@/shared/api/client'

export function hasNoLink(status: AccountStatus | null | undefined): boolean {
  return status === 'error' || status === 'disconnected' || status === 'pending'
}

/** Код ответа 409, которым сервер отклоняет отправку при аккаунте без связи. */
export const ACCOUNT_OFFLINE = 'account_offline'

/**
 * Это отказ «у аккаунта нет связи»? Узнаём по коду, а не по тексту: текст у менеджера и
 * у руководителя разный. Такой ответ значит ещё и «интерфейс отстал»: чат считал аккаунт
 * рабочим — значит, событие об обрыве не дошло, и состояние надо перечитать самим.
 */
export function isAccountOffline(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && error.code === ACCOUNT_OFFLINE
}

/**
 * Аккаунт удалён руководителем (сервер ставит `disconnected` только при удалении).
 * Вернуть его кнопкой «Переподключить» нельзя — в списке «Аккаунты» такой записи нет.
 */
export function isAccountDeleted(status: AccountStatus | null | undefined): boolean {
  return status === 'disconnected'
}

/** Куда ведёт «Переподключить»: раздел «Аккаунты» уже с фильтром «требуют внимания». */
export const RECONNECT_HREF = '/profile/accounts?attention=1'

/**
 * Показывать ли «Переподключить». Только руководителю и только там, где есть что
 * переподключать: у удалённого аккаунта кнопка вела бы в список без этой записи.
 */
export function canReconnect(status: AccountStatus | null | undefined, isAdmin: boolean): boolean {
  return isAdmin && hasNoLink(status) && !isAccountDeleted(status)
}

/** Одним словом: что случилось с аккаунтом. Для баннера и поля ввода. */
function offlineWord(status: AccountStatus | null | undefined): string {
  if (isAccountDeleted(status)) return 'удалён'
  if (status === 'pending') return 'не подключён'
  return 'отключён'
}

/**
 * Текст баннера над полем ввода. Руководителю не предлагаем сообщить самому себе.
 * `status` необязателен: старый сервер его не присылает — тогда говорим «отключён».
 */
export function offlineBannerText(
  accountTitle: string,
  isAdmin: boolean,
  status?: AccountStatus | null,
): string {
  const base = `Аккаунт «${accountTitle}» ${offlineWord(status)} — сообщения не уходят.`
  return isAdmin ? base : `${base} Сообщите руководителю`
}

/** Пояснение в самом поле ввода, пока оно выключено. Короткое: на телефоне поле узкое. */
export const OFFLINE_PLACEHOLDER = 'Аккаунт отключён'

/** Причина, по которой нельзя писать клиенту, — для поля ввода (`Composer.disabledReason`). */
export function offlineReason(status?: AccountStatus | null): string {
  return `Аккаунт ${offlineWord(status)}`
}

/**
 * Подсказка в поле ввода при аккаунте без связи. Служебная заметка в Telegram не уходит,
 * и сервер принимает её и без связи — поэтому поле не мёртвое, а сразу говорит, что можно.
 */
export function blockedPlaceholder(reason: string): string {
  return `${reason} — только заметки`
}

/** Почему файл не прикрепился: его бросили или вставили в чат, где отправка закрыта. */
export function blockedAttachText(reason: string): string {
  return `Файл не прикреплён: ${reason.toLowerCase()}, клиенту сейчас ничего не отправить`
}
