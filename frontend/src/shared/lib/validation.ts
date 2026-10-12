/**
 * Проверка вводимых данных на месте — до запроса на сервер.
 *
 * Правила зеркалят серверные (backend/app/schemas/validators.py, пункт L1
 * замечаний от 11.10.2026): сервер остаётся последней линией, а здесь человек
 * получает ту же подсказку сразу под полем, без похода по сети. Функции
 * чистые, без обращения к часам и DOM — их можно проверять тестами.
 */

/** Длина колонок телефона и текстовых полей: совпадает с сервером. */
export const PHONE_MAX_LENGTH = 32
export const NAME_MAX_LENGTH = 120
export const CITY_MAX_LENGTH = 120

const PHONE_MIN_DIGITS = 7
const PHONE_MAX_DIGITS = 15

export const PHONE_HINT = 'Цифры, плюс только в начале, пробелы и дефисы'
export const PHONE_ERROR = `Проверьте формат телефона: цифры, плюс только в начале, пробелы и дефисы; от ${PHONE_MIN_DIGITS} до ${PHONE_MAX_DIGITS} цифр`

/** Раньше этой даты клиент родиться не мог — отсекает опечатки вроде 0199. */
export const BIRTH_DATE_MIN = '1900-01-01'

// Плюс — один и только первым; начинается и заканчивается цифрой, внутри цифры,
// пробелы и дефисы. Скобки не принимаем осознанно (так же, как сервер).
const PHONE_FORMAT = /^\+?[0-9](?:[0-9 -]*[0-9])?$/

/** Повторные пробелы и дефисы — в один, края обрезаны. Так же делает сервер. */
export function normalizePhone(raw: string): string {
  return raw.trim().replace(/\s+/g, ' ').replace(/-{2,}/g, '-')
}

/**
 * Что не так с телефоном, либо null. Пустой телефон — не ошибка: поле
 * необязательное, пустое значение очищает его.
 */
export function phoneError(raw: string): string | null {
  const text = normalizePhone(raw)
  if (!text) return null
  if (text.length > PHONE_MAX_LENGTH) return `Телефон — не длиннее ${PHONE_MAX_LENGTH} символов`
  if (!PHONE_FORMAT.test(text)) return PHONE_ERROR
  const digits = text.replace(/\D/g, '').length
  if (digits < PHONE_MIN_DIGITS || digits > PHONE_MAX_DIGITS) return PHONE_ERROR
  return null
}

/** Короткий текст: длина считается без пробелов по краям, как на сервере. */
export function textError(raw: string, maxLength: number, label: string): string | null {
  const text = raw.trim()
  if (text.length > maxLength) return `${label} — не длиннее ${maxLength} символов`
  // Управляющие символы, кроме переноса строки и табуляции, — как на сервере.
  // eslint-disable-next-line no-control-regex -- намеренно: управляющие символы в имени недопустимы
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/.test(text)) {
    return 'В тексте есть недопустимые символы'
  }
  return null
}

function isRealDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  )
}

/** Сегодняшняя дата по часам устройства в виде `ГГГГ-ММ-ДД` — верхняя граница даты рождения. */
export function todayIso(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${String(now.getFullYear()).padStart(4, '0')}-${month}-${day}`
}

/**
 * Что не так с датой рождения, либо null. Дата — строка `ГГГГ-ММ-ДД` из
 * `<input type="date">`; пустая строка означает «не указана» и ошибкой не считается.
 */
export function birthDateError(value: string, today: string = todayIso()): string | null {
  if (!value) return null
  if (!isRealDate(value)) return 'Дата рождения указана неверно'
  if (value < BIRTH_DATE_MIN) return 'Дата рождения не может быть раньше 1900 года'
  if (value > today) return 'Дата рождения не может быть в будущем'
  return null
}
