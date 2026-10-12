/**
 * Правила экрана «Профиль» и уведомлений — чистые функции без React.
 *
 * Вынесены отдельно, чтобы проверяться тестами: каждое из них раньше жило
 * внутри разметки, и ошибка в нём была заметна только глазами.
 */

import type { Account, AppNotification, StaffMember } from '@/entities/types'
import { plural } from '@/shared/lib/format'

/**
 * Куда ведёт строка уведомления. `null` — вести некуда, строка не кликабельна.
 *
 * - оплата (сделка) → карточка сделки; если сделка недоступна, экран сделки сам
 *   покажет, что её не найти;
 * - аккаунт → у руководителя раздел «Аккаунты», у менеджера «Профиль»: раздела
 *   «Аккаунты» у него нет (`AdminOnly`), а свои аккаунты он видит в профиле.
 */
export function notificationTarget(
  notification: Pick<AppNotification, 'entity_type' | 'entity_id'>,
  isAdmin: boolean,
): string | null {
  const id = notification.entity_id
  if (id === null || !Number.isInteger(id) || id <= 0) return null
  switch (notification.entity_type) {
    case 'deal':
      return `/payments/${id}`
    case 'account':
      return isAdmin ? '/profile/accounts' : '/profile'
    default:
      return null
  }
}

/**
 * Плашка «аккаунт требует внимания» в профиле.
 *
 * Руководителю — ссылка в «Аккаунты» с включённым фильтром. Менеджеру ссылки
 * нет: он не может переподключить аккаунт, ему остаётся сообщить руководителю.
 */
export function accountAttentionBanner(
  count: number,
  isAdmin: boolean,
): { text: string; href: string | null } {
  if (isAdmin) {
    return {
      text: `${count} ${plural(count, 'аккаунт требует', 'аккаунта требуют', 'аккаунтов требуют')} внимания`,
      href: '/profile/accounts?attention=1',
    }
  }
  return {
    text:
      count === 1
        ? 'Аккаунт отключён, сообщите руководителю'
        : `${count} ${plural(count, 'аккаунт отключён', 'аккаунта отключены', 'аккаунтов отключены')}, сообщите руководителю`,
    href: null,
  }
}

/**
 * Сколько аккаунтов менеджера сейчас не работают (нет связи, ждёт входа, отключён).
 * Считается по списку «Мои аккаунты», а не по числу из профиля: список обновляется
 * сразу, как приходит событие о состоянии аккаунта, а профиль — раз в минуту.
 */
export function accountsWithoutLink(accounts: Pick<Account, 'status'>[]): number {
  return accounts.filter((account) => account.status !== 'connected').length
}

/** Фильтр «Только требующие внимания» на странице «Аккаунты» живёт в адресе: `?attention=1`. */
export function attentionFilterOn(params: URLSearchParams): boolean {
  return params.get('attention') === '1'
}

/** Адрес с включённым или снятым фильтром; остальные параметры не трогает. */
export function withAttentionFilter(params: URLSearchParams, on: boolean): URLSearchParams {
  const next = new URLSearchParams(params)
  if (on) next.set('attention', '1')
  else next.delete('attention')
  return next
}

/**
 * «Без аккаунта» в списке сотрудников: активный менеджер, которому не назначен
 * ни один аккаунт. Руководителю аккаунт не нужен, отключённый не работает.
 * Те же условия проверяет фильтр на сервере (`user_service.no_account_clauses`).
 */
export function isWithoutAccount(
  member: Pick<StaffMember, 'role' | 'is_active' | 'accounts'>,
): boolean {
  return member.role === 'manager' && member.is_active && member.accounts.length === 0
}

/**
 * Подпись под переключателем «Приём заявок».
 *
 * Что флаг делает на самом деле: только когда на аккаунте ровно один менеджер,
 * новый диалог сразу закрепляется за ним (включён) либо остаётся без
 * ответственного (выключен). Если менеджеров несколько, флаг ничего не меняет:
 * диалог и так ничей. Поэтому подпись зависит от того, на каких аккаунтах
 * работает человек. `accounts === null` — аккаунты ещё не загрузились.
 */
export function acceptingLeadsHint(
  enabled: boolean,
  accounts: { managers: { id: number }[] }[] | null,
  userId: number,
): string {
  if (accounts === null) {
    return enabled
      ? 'Если вы единственный менеджер аккаунта, новые диалоги на нём закрепляются за вами сразу.'
      : 'Даже если вы единственный менеджер аккаунта, новые диалоги на нём за вами не закрепляются.'
  }

  const mine = accounts.filter((account) => account.managers.some((m) => m.id === userId))
  if (mine.length === 0) {
    return 'Вы не назначены ни на один аккаунт — переключатель пока ни на что не влияет.'
  }

  const alone = mine.filter((account) => account.managers.length === 1)
  if (alone.length === 0) {
    return 'Пока ничего не меняет: на ваших аккаунтах работает несколько менеджеров, новые диалоги и так не закрепляются автоматически.'
  }

  return enabled
    ? 'Новые диалоги на аккаунтах, где вы работаете один, закрепляются за вами сразу.'
    : 'Новые диалоги на аккаунтах, где вы работаете один, за вами не закрепляются — они остаются без ответственного, их можно взять из очереди.'
}
