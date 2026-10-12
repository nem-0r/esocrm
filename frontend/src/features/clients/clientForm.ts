/**
 * Форма «Данные клиента»: проверка и сборка тела PATCH.
 *
 * Правила — серверные (L1, 11.10.2026), см. `shared/lib/validation`. Проверяются
 * и отправляются только изменённые поля: клиент, сохранённый до появления этих
 * правил (телефон в старом формате, длинное имя), не должен мешать поправить
 * соседнее поле — ошибка вылезет только у того, что человек сам тронул.
 */

import type { BirthTimeApprox } from '@/entities/types'
import {
  CITY_MAX_LENGTH,
  NAME_MAX_LENGTH,
  birthDateError,
  normalizePhone,
  phoneError,
  textError,
} from '@/shared/lib/validation'

import type { ClientPatch } from '@/features/clients/queries'

export interface ClientFormValues {
  display_name: string
  phone: string
  birth_date: string
  birth_time: string
  birth_time_approx: string
  birth_city: string
}

/** Как форма приводит короткий текст перед отправкой: края срезаны, серии пробелов — один пробел. */
function squash(value: string): string {
  return value.trim().replace(/\s+/g, ' ')
}

export const changed = {
  display_name: (form: ClientFormValues, initial: ClientFormValues) =>
    squash(form.display_name) !== squash(initial.display_name),
  phone: (form: ClientFormValues, initial: ClientFormValues) =>
    normalizePhone(form.phone) !== normalizePhone(initial.phone),
  birth_date: (form: ClientFormValues, initial: ClientFormValues) =>
    form.birth_date !== initial.birth_date,
  birth_city: (form: ClientFormValues, initial: ClientFormValues) =>
    squash(form.birth_city) !== squash(initial.birth_city),
}

/** Ошибки по именам полей формы; пустой объект — можно отправлять. */
export function validateClientForm(
  form: ClientFormValues,
  initial: ClientFormValues,
  today?: string,
): Record<string, string> {
  const problems: Record<string, string> = {}
  if (changed.display_name(form, initial)) {
    const problem = textError(form.display_name, NAME_MAX_LENGTH, 'Имя')
    if (problem) problems.display_name = problem
  }
  if (changed.phone(form, initial)) {
    const problem = phoneError(form.phone)
    if (problem) problems.phone = problem
  }
  if (changed.birth_date(form, initial)) {
    const problem = birthDateError(form.birth_date, today)
    if (problem) problems.birth_date = problem
  }
  if (changed.birth_city(form, initial)) {
    const problem = textError(form.birth_city, CITY_MAX_LENGTH, 'Город рождения')
    if (problem) problems.birth_city = problem
  }
  return problems
}

/** Тело PATCH: пустое поле — `null` (очистить), нетронутое — вовсе не отправляется. */
export function buildClientPatch(form: ClientFormValues, initial: ClientFormValues): ClientPatch {
  const patch: ClientPatch = {
    // Точное время вытесняет приблизительное, как и на сервере.
    birth_time: form.birth_time || null,
    birth_time_approx:
      form.birth_time || form.birth_time_approx === 'none'
        ? null
        : (form.birth_time_approx as BirthTimeApprox),
  }
  if (changed.display_name(form, initial)) patch.display_name = squash(form.display_name) || null
  if (changed.phone(form, initial)) patch.phone = normalizePhone(form.phone) || null
  if (changed.birth_date(form, initial)) patch.birth_date = form.birth_date || null
  if (changed.birth_city(form, initial)) patch.birth_city = squash(form.birth_city) || null
  return patch
}
