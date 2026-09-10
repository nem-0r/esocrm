import { AlertCircle, Check, CheckCheck, Clock, Download, FileText, Lock, Pencil } from 'lucide-react'
import { useState } from 'react'

import type { Message } from '@/entities/types'
import { ImagePreview, VideoPreview } from '@/features/chats/components/MediaAttachment'
import { VoicePlayer } from '@/features/chats/components/VoicePlayer'
import { ApiError } from '@/shared/api/client'
import { useMe } from '@/shared/hooks/useAuth'
import { cn } from '@/shared/lib/cn'
import { fileSize, time } from '@/shared/lib/format'
import { Button, InlineError, Textarea } from '@/shared/ui'

/** Столько же разрешает сам Telegram — после этого срока правка отклонится сервером. */
const EDIT_WINDOW_HOURS = 48

function canEditMessage(message: Message, myUserId: number): boolean {
  // Тот же порядок, что и на бэкенде (message_service.edit_message):
  // сообщение может провисеть в очереди до отправки, и окно правки Telegram
  // отсчитывается от факта отправки, а не от момента постановки в очередь.
  const sentAt = message.sent_at ?? message.created_at
  return (
    message.direction === 'out' &&
    message.author?.id === myUserId &&
    message.kind === 'text' &&
    (message.status === 'sent' || message.status === 'read') &&
    Date.now() - new Date(sentAt).getTime() < EDIT_WINDOW_HOURS * 3600_000
  )
}

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
  onEdit,
  savingEdit,
}: {
  message: Message
  onRetry?: () => void
  retrying?: boolean
  onEdit?: (text: string) => Promise<unknown>
  savingEdit?: boolean
}) {
  const me = useMe()
  const outgoing = message.direction === 'out'
  const editable = !message.is_internal && canEditMessage(message, me.id) && Boolean(onEdit)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.text ?? '')
  const [editError, setEditError] = useState<string | null>(null)

  function startEdit() {
    setDraft(message.text ?? '')
    setEditError(null)
    setEditing(true)
  }

  async function submitEdit() {
    const text = draft.trim()
    if (!text || !onEdit) return
    setEditError(null)
    try {
      await onEdit(text)
      setEditing(false)
    } catch (cause) {
      setEditError(cause instanceof ApiError ? cause.message : 'Не удалось сохранить изменения')
    }
  }

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

        {message.attachments.map((file) => {
          if (file.mime_type?.startsWith('audio/')) {
            return (
              <VoicePlayer key={file.id} src={file.url} durationSec={file.duration_sec ?? null} />
            )
          }
          if (file.mime_type?.startsWith('image/')) {
            return (
              <ImagePreview
                key={file.id}
                src={file.url}
                width={file.width}
                height={file.height}
                alt={file.file_name}
              />
            )
          }
          if (file.mime_type?.startsWith('video/')) {
            return (
              <VideoPreview key={file.id} src={file.url} width={file.width} height={file.height} />
            )
          }
          return (
            <a
              key={file.id}
              href={file.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-2 rounded bg-black/25 px-2.5 py-2 transition-colors hover:bg-black/40"
            >
              <FileText className="size-4 shrink-0 text-accent-text" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium text-ink">
                  {file.file_name}
                </span>
                <span className="block text-micro text-ink-faint">{fileSize(file.size_bytes)}</span>
              </span>
              <Download className="size-4 shrink-0 text-ink-faint" aria-hidden />
            </a>
          )
        })}

        {editing ? (
          <div className="flex flex-col gap-1.5">
            <Textarea
              autoFocus
              rows={2}
              value={draft}
              maxLength={4096}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void submitEdit()
                }
                if (event.key === 'Escape') setEditing(false)
              }}
              className="bg-surface text-sm"
            />
            {editError && <InlineError message={editError} />}
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditing(false)}
                className="text-micro text-ink-faint transition-colors hover:text-ink"
              >
                Отмена
              </button>
              <Button
                size="sm"
                onClick={() => void submitEdit()}
                disabled={!draft.trim() || savingEdit}
                loading={savingEdit}
              >
                Сохранить
              </Button>
            </div>
          </div>
        ) : (
          message.text && (
            <span
              className={cn(
                'whitespace-pre-wrap break-words text-sm',
                outgoing ? 'text-accent-text' : 'text-ink',
              )}
            >
              {message.text}
            </span>
          )
        )}

        {!editing && (
          <span className="flex items-center justify-end gap-1.5 text-micro text-ink-faint">
            {editable && (
              <button
                aria-label="Изменить сообщение"
                onClick={startEdit}
                className="relative text-ink-faint transition-colors hover:text-ink before:absolute before:-inset-2 before:content-['']"
              >
                <Pencil className="size-3" aria-hidden />
              </button>
            )}
            {message.edited_at && <span>изменено</span>}
            <span className="tnum">{time(message.created_at)}</span>
            {outgoing && <StatusIcon status={message.status} />}
          </span>
        )}

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
