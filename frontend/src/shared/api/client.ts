/**
 * Единственная точка обращения к серверу.
 *
 * Главное правило проекта: интерфейс НИКОГДА не подменяет ошибку демо-данными.
 * В прошлой версии продукта фронтенд при ответе «не авторизован» молча показывал
 * моки, и всё «работало» — это самый дорогой класс багов. Здесь любая ошибка
 * поднимается наверх как ApiError и обязана быть показана пользователю.
 */

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> }
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  get isUnauthorized() {
    return this.status === 401
  }

  get isNotFound() {
    return this.status === 404
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

const BASE = '/api/v1'

function buildUrl(path: string, query?: Query): string {
  const url = `${BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '') continue
    params.append(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

/** Сюда подписывается корневой компонент, чтобы выкинуть на вход при истёкшей сессии. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler
}

async function parseError(response: Response): Promise<ApiError> {
  let code = 'error'
  let message = 'Не удалось выполнить запрос'
  let details: Record<string, unknown> = {}
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>
    if (body?.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
      details = body.error.details ?? {}
    }
  } catch {
    // Тело не JSON — оставляем текст по умолчанию, но статус сохраняем.
  }
  if (response.status === 0 || response.status >= 500) {
    message = message === 'Не удалось выполнить запрос' ? 'Сервер недоступен' : message
  }
  return new ApiError(response.status, code, message, details)
}

async function request<T>(
  method: string,
  path: string,
  options: {
    query?: Query
    body?: unknown
    signal?: AbortSignal
    raw?: boolean
    /** Нужен сам ответ, а не тело: у выгрузок имя файла лежит в заголовке. */
    rawResponse?: boolean
    /** Проверка сессии сама обрабатывает 401 — глобальный обработчик её не касается. */
    skipUnauthorizedHandler?: boolean
  } = {},
): Promise<T> {
  let response: Response
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      credentials: 'include',
      headers: options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
      body:
        options.body instanceof FormData
          ? options.body
          : options.body !== undefined
            ? JSON.stringify(options.body)
            : undefined,
      signal: options.signal,
    })
  } catch (cause) {
    if ((cause as Error)?.name === 'AbortError') throw cause
    throw new ApiError(0, 'network', 'Нет связи с сервером. Проверьте подключение.')
  }

  if (!response.ok) {
    const error = await parseError(response)
    if (error.isUnauthorized && !options.skipUnauthorizedHandler) onUnauthorized?.()
    throw error
  }

  if (options.rawResponse) return response as T
  if (response.status === 204) return undefined as T
  if (options.raw) return (await response.blob()) as T
  return (await response.json()) as T
}

/** Имя файла из заголовка ответа: сервер знает его лучше, чем экран.
 *
 * Русское имя едет в `filename*=UTF-8''` (заголовок HTTP обязан быть latin-1),
 * поэтому его читаем первым, а `filename="..."` — запасной латинский вариант.
 */
export function filenameFromResponse(response: Response, fallback: string): string {
  const header = response.headers.get('content-disposition') ?? ''
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim())
    } catch {
      // Битая процентная последовательность — не повод не отдать файл.
    }
  }
  const plain = /filename="([^"]+)"/i.exec(header)
  return plain ? plain[1] : fallback
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    request<T>('GET', path, { query, signal }),
  /** Тихая проверка сессии: 401 здесь не ошибка, а ответ «не авторизован». */
  probe: <T>(path: string, signal?: AbortSignal) =>
    request<T>('GET', path, { signal, skipUnauthorizedHandler: true }),
  post: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>('POST', path, { body, query }),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body }),
  del: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, { body: form }),
  blob: (path: string, query?: Query) => request<Blob>('GET', path, { query, raw: true }),
  file: (path: string, query?: Query) => request<Response>('GET', path, { query, rawResponse: true }),
}

/** Скачивание файла с проверкой прав на сервере — не прямая ссылка на хранилище.
 *
 * Имя берём из заголовка ответа, `filename` — только запасной вариант: иначе
 * файл на диске и файл, который отдал сервер, называются по-разному.
 */
export async function downloadFile(path: string, filename: string, query?: Query) {
  const response = await api.file(path, query)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filenameFromResponse(response, filename)
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
