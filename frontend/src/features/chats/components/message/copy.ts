/**
 * Копирование из CRM в буфер обмена: текст сообщения, картинка, несколько
 * сообщений разом — чтобы вставить в Telegram, почту или документ.
 */

import type { Message } from '@/entities/types'
import { time } from '@/shared/lib/format'

export async function copyText(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  // Старые браузеры и страница не по https: через временное поле.
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  const done = document.execCommand('copy')
  area.remove()
  if (!done) throw new Error('Браузер не дал скопировать')
}

export function canCopyImage(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.ClipboardItem !== 'undefined' &&
    Boolean(navigator.clipboard?.write)
  )
}

async function toPng(blob: Blob): Promise<Blob> {
  if (blob.type === 'image/png') return blob
  const bitmap = await createImageBitmap(blob)
  const canvas = document.createElement('canvas')
  canvas.width = bitmap.width
  canvas.height = bitmap.height
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Нет холста')
  context.drawImage(bitmap, 0, 0)
  return new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((png) => (png ? resolve(png) : reject(new Error('Не вышло'))), 'image/png'),
  )
}

/**
 * Картинку буфер обмена принимает только в PNG. Safari требует создать
 * ClipboardItem сразу в обработчике нажатия, а содержимое отдать обещанием —
 * поэтому загрузка и перекодирование идут внутри Promise.
 */
export async function copyImage(url: string): Promise<void> {
  const png = (async () => {
    const response = await fetch(url, { credentials: 'include' })
    if (!response.ok) throw new Error('Файл недоступен')
    return toPng(await response.blob())
  })()
  await navigator.clipboard.write([new ClipboardItem({ 'image/png': png })])
}

/** Несколько сообщений одним текстом — как Telegram копирует выделенное. */
export function formatForCopy(messages: Message[], clientName: string): string {
  return messages
    .map((message) => {
      const who =
        message.direction === 'in'
          ? clientName
          : message.author?.full_name ?? (message.author_kind === 'userbot' ? 'Рассылка воронки' : 'Менеджер')
      const files = message.attachments.map((file) => `[${file.file_name}]`).join(' ')
      const body = [message.text ?? '', files].filter(Boolean).join('\n')
      return `${who}, ${time(message.created_at)}:\n${body}`
    })
    .join('\n\n')
}
