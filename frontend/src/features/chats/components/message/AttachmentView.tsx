import { AlertCircle, Download, FileText, Loader2 } from 'lucide-react'

import type { AttachmentRef } from '@/entities/types'
import { ImagePreview, VideoPreview } from '@/features/chats/components/MediaAttachment'
import { VoicePlayer } from '@/features/chats/components/VoicePlayer'
import { downloadFile } from '@/shared/api/client'
import { fileSize } from '@/shared/lib/format'
import { toastError } from '@/shared/ui'

/** Вид вложения: с сервера, а у старых записей — по типу файла. */
export function kindOf(file: AttachmentRef): NonNullable<AttachmentRef['kind']> {
  if (file.kind) return file.kind
  const mime = file.mime_type ?? ''
  if (mime.startsWith('image/')) return 'photo'
  if (mime.startsWith('video/')) return 'video'
  if (mime.startsWith('audio/')) return 'voice'
  return 'document'
}

/**
 * Одно вложение в пузыре сообщения. Правило — ни одного пустого места:
 * пока файл докачивается из Telegram, видно «загружается»; если CRM его не
 * хранит (слишком большой, не скачался) — видно, что файл есть и где его открыть.
 */
export function AttachmentView({ file }: { file: AttachmentRef }) {
  const status = file.status ?? 'ready'
  if (status !== 'ready') return <UnavailableFile file={file} />

  const kind = kindOf(file)
  const mime = file.mime_type ?? ''

  if (kind === 'voice' || kind === 'audio') {
    return (
      <VoicePlayer
        src={file.url}
        mimeType={file.mime_type}
        durationSec={file.duration_sec ?? null}
        waveform={kind === 'voice' ? (file.waveform ?? null) : null}
        title={kind === 'audio' ? (file.title ?? file.file_name) : null}
        performer={kind === 'audio' ? (file.performer ?? null) : null}
      />
    )
  }
  if (kind === 'sticker') {
    // Анимированный стикер Telegram (TGS) браузер не нарисует — показываем его эмодзи.
    if (mime.startsWith('image/')) {
      return <img src={file.url} alt={file.emoji ?? 'Стикер'} loading="lazy" className="size-36" />
    }
    if (mime.startsWith('video/')) {
      return <VideoPreview src={file.url} width={160} height={160} variant="loop" />
    }
    return (
      <span className="flex items-center gap-2 text-sm text-ink-muted">
        <span className="text-4xl leading-none">{file.emoji ?? '🙂'}</span>
        стикер
      </span>
    )
  }
  if (kind === 'photo' || (kind === 'animation' && mime.startsWith('image/'))) {
    return <ImagePreview src={file.url} width={file.width} height={file.height} alt={file.file_name} />
  }
  if (kind === 'video_note') {
    return <VideoPreview src={file.url} poster={file.thumb_url} variant="round" />
  }
  if (kind === 'animation' || kind === 'video') {
    return (
      <VideoPreview
        src={file.url}
        width={file.width}
        height={file.height}
        poster={file.thumb_url}
        variant={kind === 'animation' ? 'loop' : 'video'}
      />
    )
  }
  return (
    <button
      type="button"
      onClick={() => {
        // Не прямая ссылка: на ошибке (файл не найден, сессия истекла) браузер
        // открыл бы вместо CRM технический ответ сервера — уходить некуда.
        void downloadFile(`/files/${file.id}`, file.file_name, { download: 1 }).catch(() =>
          toastError('Не удалось скачать файл — попробуйте ещё раз'),
        )
      }}
      className="flex items-center gap-2 rounded bg-black/25 px-2.5 py-2 text-left transition-colors hover:bg-black/40"
    >
      <FileText className="size-4 shrink-0 text-accent-text" aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-ink">{file.file_name}</span>
        <span className="block text-micro text-ink-faint">{fileSize(file.size_bytes)}</span>
      </span>
      <Download className="size-4 shrink-0 text-ink-faint" aria-hidden />
    </button>
  )
}

function UnavailableFile({ file }: { file: AttachmentRef }) {
  const status = file.status ?? 'ready'
  // Причину (нет места на диске, настройка «Файлы из истории») показываем как есть:
  // без неё менеджер видел бы вечное «Загружается…» или «слишком большой».
  const label =
    status === 'pending'
      ? (file.error ?? 'Загружается из Telegram…')
      : status === 'too_large'
        ? (file.error ?? 'Слишком большой для CRM — откройте в Telegram')
        : `Не удалось скачать${file.error ? `: ${file.error}` : ''} — откройте в Telegram`
  return (
    <div className="flex items-center gap-2 rounded bg-black/25 px-2.5 py-2">
      {status === 'pending' && !file.error ? (
        <Loader2 className="size-4 shrink-0 animate-spin text-accent-text" aria-hidden />
      ) : (
        <AlertCircle className="size-4 shrink-0 text-warning" aria-hidden />
      )}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-ink">{file.file_name}</span>
        <span className="block text-micro text-ink-faint">
          {file.size_bytes ? `${fileSize(file.size_bytes)} · ` : ''}
          {label}
        </span>
      </span>
    </div>
  )
}
