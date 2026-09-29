import { AlertCircle, Check, CheckCheck, Circle, CircleCheck, Clock, Lock, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { Message } from '@/entities/types'
import { AttachmentView, kindOf } from '@/features/chats/components/message/AttachmentView'
import { ForwardedHeader, MetaCard, hasMetaCard } from '@/features/chats/components/message/MetaCards'
import { MessageMenu, type MessageActions } from '@/features/chats/components/message/MessageMenu'
import { canCopyImage, copyImage, copyText } from '@/features/chats/components/message/copy'
import { ApiError, downloadFile } from '@/shared/api/client'
import { useMe } from '@/shared/hooks/useAuth'
import { cn } from '@/shared/lib/cn'
import { time } from '@/shared/lib/format'
import { Button, InlineError, Textarea, toast, toastError } from '@/shared/ui'

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
    Date.now() - new Date(sentAt).getTime() < EDIT_WINDOW_HOURS * 3600_000 &&
    // Настоящая пересылка Telegram (не копия) — Telegram не даёт редактировать
    // пересланные сообщения, поэтому и в CRM не предлагаем «Изменить».
    !message.meta?.forwarded?.native
  )
}

/** Переслать можно то, что есть в Telegram или хотя бы в CRM: не служебную заметку. */
export function canForward(message: Message): boolean {
  if (message.is_internal) return false
  return Boolean(message.text || message.attachments.length)
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

export interface Selection {
  active: boolean
  selected: boolean
  onToggle: () => void
  onStart: () => void
}

export function MessageBubble({
  message,
  onRetry,
  retrying,
  onEdit,
  savingEdit,
  onForward,
  selection,
}: {
  message: Message
  onRetry?: () => void
  retrying?: boolean
  onEdit?: (text: string) => Promise<unknown>
  savingEdit?: boolean
  onForward?: () => void
  selection?: Selection
}) {
  const me = useMe()
  const outgoing = message.direction === 'out'
  const editable = !message.is_internal && canEditMessage(message, me.id) && Boolean(onEdit)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.text ?? '')
  const [editError, setEditError] = useState<string | null>(null)
  const meta = message.meta ?? null
  const deleted = Boolean(meta?.deleted_in_telegram_at)
  const selecting = Boolean(selection?.active)

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

  const photo = message.attachments.find(
    (file) => (file.status ?? 'ready') === 'ready' && kindOf(file) === 'photo',
  )
  const downloadable = message.attachments.filter((file) => (file.status ?? 'ready') === 'ready')
  const actions: MessageActions = {
    onCopyText: message.text
      ? () =>
          void copyText(message.text ?? '').then(
            () => toast('Текст скопирован'),
            () => toastError('Не удалось скопировать — выделите текст вручную'),
          )
      : undefined,
    onCopyImage:
      photo && canCopyImage()
        ? () =>
            void copyImage(photo.url).then(
              () => toast('Изображение скопировано'),
              () => toastError('Не удалось скопировать изображение'),
            )
        : undefined,
    // Несколько файлов (альбом) — пункт на каждый, с именем: иначе не понять,
    // что именно скачивается. Один файл — один пункт «Скачать», как раньше.
    downloads: downloadable.length
      ? downloadable.map((file) => ({
          label: downloadable.length > 1 ? `Скачать «${file.file_name}»` : 'Скачать',
          run: () => {
            // Не прямая навигация: на ошибке (файл не найден, сессия истекла)
            // браузер открыл бы вместо CRM технический ответ сервера.
            void downloadFile(`/files/${file.id}`, file.file_name, { download: 1 }).catch(() =>
              toastError('Не удалось скачать файл — попробуйте ещё раз'),
            )
          },
        }))
      : undefined,
    onForward: onForward && canForward(message) ? onForward : undefined,
    onSelect: selection && !message.is_internal ? selection.onStart : undefined,
    onEdit: editable ? startEdit : undefined,
  }

  const checkbox = selecting && !message.is_internal && (
    <span className="flex w-6 shrink-0 items-center justify-center" aria-hidden>
      {selection?.selected ? (
        <CircleCheck className="size-5 text-accent-text" />
      ) : (
        <Circle className="size-5 text-ink-faint" />
      )}
    </span>
  )

  // Служебная заметка: клиент её не видит, поэтому и выглядит она иначе.
  if (message.is_internal) {
    return (
      <div className="flex justify-center px-4">
        <div className="flex max-w-[80%] items-start gap-2 rounded-md border border-dashed border-line-strong bg-surface px-3 py-2">
          <Lock className="mt-0.5 size-3.5 shrink-0 text-ink-faint" aria-hidden />
          <div className="flex min-w-0 flex-col gap-1">
            <span className="text-micro uppercase tracking-wide text-ink-faint">
              Служебная заметка · клиент не видит
            </span>
            {message.attachments.map((file) => (
              <AttachmentView key={file.id} file={file} />
            ))}
            {message.text && (
              <span className="whitespace-pre-wrap break-words text-sm text-ink-muted">
                {message.text}
              </span>
            )}
            <span className="flex items-center gap-1.5 text-micro text-ink-faint">
              {message.author?.full_name} · {time(message.created_at)}
              <MessageMenu actions={actions} />
            </span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'group flex items-center gap-1 px-4',
        outgoing ? 'justify-end' : 'justify-start',
        selecting && 'cursor-pointer',
        selection?.selected && 'bg-accent-soft/50',
      )}
      onClick={selecting ? selection?.onToggle : undefined}
      aria-selected={selecting ? selection?.selected : undefined}
    >
      {!outgoing && checkbox}
      <div
        className={cn(
          'flex max-w-[85%] flex-col gap-1.5 rounded-lg px-3 py-2 desk:max-w-[70%]',
          outgoing ? 'bg-bubble-out' : 'bg-bubble-in',
          message.status === 'failed' && 'ring-1 ring-danger/60',
          deleted && 'opacity-60',
          selecting && 'pointer-events-none',
        )}
      >
        {message.author_kind === 'userbot' && (
          <span className="text-micro uppercase tracking-wide text-violet-text">
            Рассылка воронки
          </span>
        )}

        {meta && <ForwardedHeader meta={meta} />}

        {message.attachments.map((file) => (
          <AttachmentView key={file.id} file={file} />
        ))}

        {meta && <MetaCard meta={meta} />}

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
          message.text &&
          !hasMetaCard(meta) && (
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
            {deleted && (
              <span className="flex items-center gap-1 text-warning">
                <Trash2 className="size-3" aria-hidden />
                удалено в Telegram
              </span>
            )}
            {message.edited_at && <span>изменено</span>}
            <span className="tnum">{time(message.created_at)}</span>
            {outgoing && <StatusIcon status={message.status} />}
            {!selecting && (
              <MessageMenu
                actions={actions}
                className="desk:opacity-0 desk:group-hover:opacity-100 desk:focus-visible:opacity-100 desk:data-[state=open]:opacity-100"
              />
            )}
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
      {outgoing && checkbox}
    </div>
  )
}
