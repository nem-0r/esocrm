import { AlertCircle, Check, CheckCheck, Clock, Download, FileText, Lock } from 'lucide-react'

import type { Message } from '@/entities/types'
import { cn } from '@/shared/lib/cn'
import { fileSize, time } from '@/shared/lib/format'

/**
 * Статуса «доставлено» нет намеренно: MTProto его не отдаёт.
 * Часы — в очереди, одна галочка — отправлено, две — прочитано, крестик — ошибка.
 */
function StatusIcon({ status }: { status: Message['status'] }) {
  switch (status) {
    case 'queued':
      return <Clock className="size-3.5 text-ink-faint" aria-label="в очереди" />
    case 'sent':
      return <Check className="size-3.5 text-ink-faint" aria-label="отправлено" />
    case 'read':
      return <CheckCheck className="size-3.5 text-accent-text" aria-label="прочитано" />
    case 'failed':
      return <AlertCircle className="size-3.5 text-danger" aria-label="ошибка отправки" />
  }
}

export function MessageBubble({
  message,
  onRetry,
  retrying,
}: {
  message: Message
  onRetry?: () => void
  retrying?: boolean
}) {
  const outgoing = message.direction === 'out'

  // Служебная заметка: клиент её не видит, поэтому и выглядит она иначе.
  if (message.is_internal) {
    return (
      <div className="flex justify-center px-4">
        <div className="flex max-w-[80%] items-start gap-2 rounded-md border border-dashed border-line-strong bg-surface px-3 py-2">
          <Lock className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
          <div className="flex min-w-0 flex-col gap-0.5">
            <span className="text-micro uppercase tracking-wide text-ink-faint">
              Служебная заметка · клиент не видит
            </span>
            <span className="whitespace-pre-wrap break-words text-sm text-ink-muted">
              {message.text}
            </span>
            <span className="text-micro text-ink-faint">
              {message.author?.full_name} · {time(message.created_at)}
            </span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={cn('flex px-4', outgoing ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'flex max-w-[85%] flex-col gap-1.5 rounded-lg px-3 py-2 desk:max-w-[70%]',
          outgoing ? 'bg-bubble-out' : 'bg-bubble-in',
          message.status === 'failed' && 'ring-1 ring-danger/60',
        )}
      >
        {message.author_kind === 'userbot' && (
          <span className="text-micro uppercase tracking-wide text-violet-text">
            Рассылка воронки
          </span>
        )}

        {message.attachments.map((file) => (
          <a
            key={file.id}
            href={file.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-2 rounded bg-black/25 px-2.5 py-2 transition-colors hover:bg-black/40"
          >
            <FileText className="size-4 shrink-0 text-accent-text" aria-hidden />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium text-ink">{file.file_name}</span>
              <span className="block text-micro text-ink-faint">{fileSize(file.size_bytes)}</span>
            </span>
            <Download className="size-4 shrink-0 text-ink-faint" aria-hidden />
          </a>
        ))}

        {message.text && (
          <span
            className={cn(
              'whitespace-pre-wrap break-words text-sm',
              outgoing ? 'text-accent-text' : 'text-ink',
            )}
          >
            {message.text}
          </span>
        )}

        <span className="flex items-center justify-end gap-1 text-micro text-ink-faint">
          {message.edited_at && <span>изменено</span>}
          <span className="tnum">{time(message.created_at)}</span>
          {outgoing && <StatusIcon status={message.status} />}
        </span>

        {message.status === 'failed' && (
          <div className="flex items-center justify-end gap-2">
            <span className="text-micro text-danger">
              {message.error_text ?? 'Не удалось отправить'}
            </span>
            {onRetry && (
              <button
                onClick={onRetry}
                disabled={retrying}
                className="text-micro text-accent-text underline-offset-4 transition-colors hover:underline disabled:opacity-50"
              >
                {retrying ? 'Повторяем…' : 'Повторить'}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
