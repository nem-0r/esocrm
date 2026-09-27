/**
 * Что делать со вставкой из буфера обмена и перетаскиванием файлов.
 *
 * Буфер часто содержит сразу несколько представлений одного и того же, и
 * «правильный» ответ зависит от того, откуда скопировали:
 *
 * - скриншот (Cmd+Ctrl+Shift+4, Win+Shift+S) — только картинка → прикрепляем;
 * - «Копировать изображение» в браузере — картинка + HTML → прикрепляем;
 * - файл, скопированный в Finder/Проводнике, — сам файл + его имя текстом →
 *   прикрепляем файл, имя в поле ввода не нужно;
 * - ячейки Excel/Numbers — текст + картинка-снимок ячеек → вставляем ТЕКСТ:
 *   именно его ожидает человек, копировавший таблицу;
 * - обычный текст — вставляем текст (браузер сделает это сам).
 */

/** Минимально нужное от ClipboardEvent/DragEvent — чтобы проверять без браузера. */
export interface TransferLike {
  files: ArrayLike<File> | null
  getData(format: string): string
}

export interface PasteDecision {
  /** Файлы, которые нужно прикрепить. Пусто — пусть браузер вставит текст. */
  files: File[]
}

export function decidePaste(data: TransferLike | null): PasteDecision {
  if (!data) return { files: [] }
  const files = Array.from(data.files ?? []).filter((file) => file.size > 0)
  if (files.length === 0) return { files: [] }
  const text = (data.getData('text/plain') ?? '').trim()
  if (!text) return { files }
  // Скопированный файл: текстом лежит его имя (или путь) — берём файл.
  const looksLikeFileNames = files.some(
    (file) => file.name && (text === file.name || text.endsWith(`/${file.name}`) || text.includes(file.name)),
  )
  if (looksLikeFileNames) return { files }
  // Хоть один не-картинка — это точно файл, а не снимок ячеек.
  if (files.some((file) => !file.type.startsWith('image/'))) return { files }
  // Текст + картинка без связи между ними — таблица или страница: нужен текст.
  return { files: [] }
}

/**
 * Имя для вставленного скриншота. Браузеры называют его одинаково — «image.png»,
 * и в карточке клиента пять одинаковых «image.png» не отличить друг от друга.
 */
export function nameForPasted(file: File, now: Date = new Date()): string {
  const generic = !file.name || /^image\.(png|jpe?g|gif|webp)$/i.test(file.name)
  if (!generic) return file.name
  const extension = (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')
  const pad = (value: number) => String(value).padStart(2, '0')
  const stamp =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
    `${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`
  return `Скриншот ${stamp}.${extension}`
}

/** Тип вложения по файлу — только для превью до ответа сервера. Решает сервер. */
export function guessKind(file: Blob & { name?: string }): 'photo' | 'video' | 'audio' | 'document' {
  const type = file.type || ''
  if (type.startsWith('image/')) return 'photo'
  if (type.startsWith('video/')) return 'video'
  if (type.startsWith('audio/')) return 'audio'
  return 'document'
}

/** Сколько байт можно загрузить — совпадает с лимитом сервера. */
export const MAX_UPLOAD_BYTES = 200 * 1024 * 1024
