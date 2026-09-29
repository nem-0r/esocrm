import { AlertCircle, FileText, Film, Music, RotateCw, X } from 'lucide-react'

import { VoicePlayer } from '@/features/chats/components/VoicePlayer'
import { cn } from '@/shared/lib/cn'
import { fileSize } from '@/shared/lib/format'
import type { QueueItem } from './useAttachmentQueue'

/**
 * Вложения будущего сообщения: миниатюры картинок, значки файлов, плеер
 * записанного голосового, прогресс загрузки и ошибка у каждого по отдельности.
 */
export function AttachmentTray({
  items,
  onRemove,
  onRetry,
}: {
  items: QueueItem[]
  onRemove: (id: string) => void
  onRetry: (id: string) => void
}) {
  if (items.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-2 pb-2" aria-label="Вложения к сообщению">
      {items.map((item) => (
        <li
          key={item.id}
          className={cn(
            'relative flex items-center gap-2 overflow-hidden rounded bg-surface-raised',
            item.voice ? 'p-1.5' : 'px-2.5 py-1.5',
            item.status === 'error' && 'ring-1 ring-danger/60',
          )}
        >
          {item.voice && item.previewUrl ? (
            <div className="flex flex-col gap-1">
              <VoicePlayer
                src={item.previewUrl}
                durationSec={item.result?.duration_sec ?? item.durationSec}
                waveform={item.result?.waveform ?? null}
                local
              />
              {/* Причину показываем: красная рамка без слов не объясняет, что делать. */}
              {item.status === 'error' && (
                <span className="px-1 text-micro text-danger">{item.error}</span>
              )}
              {item.status === 'uploading' && (
                <span className="px-1 text-micro text-ink-faint">{uploadLabel(item.progress)}</span>
              )}
            </div>
          ) : (
            <>
              <Thumb item={item} />
              <span className="flex min-w-0 flex-col">
                <span className="max-w-40 truncate text-xs text-ink">{item.name}</span>
                <span className="text-micro text-ink-faint">
                  {item.status === 'uploading'
                    ? uploadLabel(item.progress)
                    : item.status === 'error'
                      ? item.error
                      : fileSize(item.result?.size_bytes ?? item.size)}
                </span>
              </span>
            </>
          )}

          {item.status === 'error' && (
            <button
              type="button"
              aria-label={`Повторить загрузку ${item.name}`}
              title="Повторить загрузку"
              onClick={() => onRetry(item.id)}
              className="text-ink-faint transition-colors hover:text-accent-text"
            >
              <RotateCw className="size-3.5" aria-hidden />
            </button>
          )}
          <button
            type="button"
            aria-label={`Убрать ${item.name}`}
            title="Убрать"
            onClick={() => onRemove(item.id)}
            className="text-ink-faint transition-colors hover:text-danger"
          >
            <X className="size-3.5" aria-hidden />
          </button>

          {item.status === 'uploading' && (
            <span
              className="absolute inset-x-0 bottom-0 h-0.5 bg-accent transition-all"
              style={{ width: `${Math.max(4, Math.round(item.progress * 100))}%` }}
              aria-hidden
            />
          )}
        </li>
      ))}
    </ul>
  )
}

/** Файл уже передан, но сервер ещё разбирает его (голосовое перекодируется) —
 *  «загрузка 100%» на этом этапе висела бы и выглядела как зависание. */
function uploadLabel(progress: number): string {
  return progress >= 1 ? 'обработка…' : `загрузка ${Math.round(progress * 100)}%`
}

function Thumb({ item }: { item: QueueItem }) {
  if (item.status === 'error') {
    return <AlertCircle className="size-5 shrink-0 text-danger" aria-hidden />
  }
  if (item.kind === 'photo' && item.previewUrl) {
    return (
      <img
        src={item.previewUrl}
        alt=""
        className="size-10 shrink-0 rounded object-cover"
      />
    )
  }
  if (item.kind === 'video') return <Film className="size-5 shrink-0 text-accent-text" aria-hidden />
  if (item.kind === 'audio' || item.kind === 'voice') {
    return <Music className="size-5 shrink-0 text-accent-text" aria-hidden />
  }
  return <FileText className="size-5 shrink-0 text-accent-text" aria-hidden />
}
