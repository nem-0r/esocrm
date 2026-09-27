import { useCallback, useEffect, useRef, useState } from 'react'

import type { AttachmentKind, UploadResult } from '@/entities/types'
import { ApiError, uploadWithProgress } from '@/shared/api/client'
import { fileSize } from '@/shared/lib/format'
import { MAX_UPLOAD_BYTES, guessKind, nameForPasted } from './clipboard'

/**
 * Вложения, прикреплённые к ещё не отправленному сообщению.
 *
 * Файл начинает загружаться сразу, как только его прикрепили (скрепка, вставка
 * из буфера, перетаскивание, запись голоса), — к нажатию «Отправить» он обычно
 * уже на сервере. У каждого свой прогресс и своя ошибка: один сбой не
 * блокирует остальные, его можно повторить или убрать.
 */
export interface QueueItem {
  id: string
  file: Blob
  name: string
  size: number
  kind: AttachmentKind
  /** Локальная ссылка на превью — до ответа сервера и без повторной загрузки. */
  previewUrl: string | null
  progress: number
  status: 'uploading' | 'done' | 'error'
  result: UploadResult | null
  error: string | null
  voice: boolean
  /** Длительность записи по часам диктофона — пока сервер не посчитал точную. */
  durationSec: number | null
}

let counter = 0
const nextId = () => `a${Date.now().toString(36)}${(counter++).toString(36)}`

export function useAttachmentQueue() {
  const [items, setItems] = useState<QueueItem[]>([])
  // Зеркало состояния для действий: побочные эффекты (отмена загрузки,
  // освобождение превью) нельзя делать внутри setState — в строгом режиме
  // React вызывает его дважды.
  const current = useRef<QueueItem[]>([])
  current.current = items
  const controllers = useRef(new Map<string, AbortController>())
  const urls = useRef(new Set<string>())

  const patch = useCallback((id: string, update: Partial<QueueItem>) => {
    setItems((prev) => prev.map((item) => (item.id === id ? { ...item, ...update } : item)))
  }, [])

  const start = useCallback(
    (item: QueueItem) => {
      const controller = new AbortController()
      controllers.current.set(item.id, controller)
      uploadWithProgress<UploadResult>(
        item.voice ? '/files/voice' : '/files/upload',
        item.file,
        item.voice ? 'voice' : item.name,
        (fraction) => patch(item.id, { progress: fraction }),
        controller.signal,
      )
        .then((result) =>
          patch(item.id, {
            status: 'done',
            progress: 1,
            result,
            kind: result.kind,
            name: item.voice ? item.name : result.file_name,
          }),
        )
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === 'AbortError') return
          patch(item.id, {
            status: 'error',
            error: cause instanceof ApiError ? cause.message : 'Не удалось загрузить файл',
          })
        })
        .finally(() => controllers.current.delete(item.id))
    },
    [patch],
  )

  const add = useCallback(
    (files: File[]): string | null => {
      let rejected: string | null = null
      const created: QueueItem[] = []
      for (const original of files) {
        if (original.size === 0) continue
        if (original.size > MAX_UPLOAD_BYTES) {
          rejected = `«${original.name}» — ${fileSize(original.size)}, больше 200 МБ не отправить`
          continue
        }
        const name = nameForPasted(original)
        const kind = guessKind(original)
        const previewUrl =
          kind === 'photo' || kind === 'video' ? URL.createObjectURL(original) : null
        if (previewUrl) urls.current.add(previewUrl)
        created.push({
          id: nextId(),
          file: original,
          name,
          size: original.size,
          kind,
          previewUrl,
          progress: 0,
          status: 'uploading',
          result: null,
          error: null,
          voice: false,
          durationSec: null,
        })
      }
      if (created.length) {
        setItems((prev) => [...prev, ...created])
        created.forEach(start)
      }
      return rejected
    },
    [start],
  )

  const addVoice = useCallback(
    (blob: Blob, seconds?: number) => {
      const previewUrl = URL.createObjectURL(blob)
      urls.current.add(previewUrl)
      const item: QueueItem = {
        id: nextId(),
        file: blob,
        name: 'Голосовое сообщение',
        size: blob.size,
        kind: 'voice',
        previewUrl,
        progress: 0,
        status: 'uploading',
        result: null,
        error: null,
        voice: true,
        durationSec: seconds ?? null,
      }
      setItems((prev) => [...prev, item])
      start(item)
    },
    [start],
  )

  const forget = useCallback((item: QueueItem) => {
    controllers.current.get(item.id)?.abort()
    controllers.current.delete(item.id)
    if (item.previewUrl) {
      URL.revokeObjectURL(item.previewUrl)
      urls.current.delete(item.previewUrl)
    }
  }, [])

  const remove = useCallback(
    (id: string) => {
      const item = current.current.find((candidate) => candidate.id === id)
      if (item) forget(item)
      setItems((prev) => prev.filter((candidate) => candidate.id !== id))
    },
    [forget],
  )

  const retry = useCallback(
    (id: string) => {
      const item = current.current.find((candidate) => candidate.id === id)
      if (!item) return
      const fresh: QueueItem = { ...item, status: 'uploading', progress: 0, error: null }
      setItems((prev) => prev.map((candidate) => (candidate.id === id ? fresh : candidate)))
      start(fresh)
    },
    [start],
  )

  /** После отправки: превью больше не нужны, память освобождаем. */
  const clear = useCallback(() => {
    current.current.forEach(forget)
    setItems([])
  }, [forget])

  useEffect(() => {
    const active = controllers.current
    const created = urls.current
    return () => {
      active.forEach((controller) => controller.abort())
      created.forEach((url) => URL.revokeObjectURL(url))
    }
  }, [])

  return {
    items,
    add,
    addVoice,
    remove,
    retry,
    clear,
    uploading: items.some((item) => item.status === 'uploading'),
    failed: items.some((item) => item.status === 'error'),
    uploads: items.flatMap((item) => (item.status === 'done' && item.result ? [item.result] : [])),
  }
}
