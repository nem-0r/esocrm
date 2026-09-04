import { Lock, Paperclip, Send, StickyNote, X } from 'lucide-react'
import { useRef, useState, type KeyboardEvent } from 'react'

import { api, ApiError } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { fileSize } from '@/shared/lib/format'
import { Button, IconButton, InlineError, Sheet, Textarea } from '@/shared/ui'
import { useSendMessage, useTemplates } from '@/features/chats/queries'

interface Upload {
  upload_key: string
  file_name: string
  size_bytes: number
  mime_type: string | null
}

const MAX_LENGTH = 4096

// Шаблоны сообщений выключены до второй версии (ТЗ от 01.09.2026, п. 3.1).
const TEMPLATES_ENABLED = false

export function Composer({ conversationId }: { conversationId: number }) {
  const send = useSendMessage(conversationId)
  const { data: templates } = useTemplates()
  const fileInput = useRef<HTMLInputElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)

  const [text, setText] = useState('')
  const [internal, setInternal] = useState(false)
  const [uploads, setUploads] = useState<Upload[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [templatesOpen, setTemplatesOpen] = useState(false)

  const canSend = (text.trim().length > 0 || uploads.length > 0) && !send.isPending && !uploading

  async function submit() {
    if (!canSend) return
    setError(null)
    try {
      await send.mutateAsync({
        text: text.trim() || undefined,
        is_internal: internal,
        uploads,
      })
      setText('')
      setUploads([])
      setInternal(false)
      textarea.current?.focus()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось отправить')
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void submit()
    }
  }

  async function onPickFiles(files: FileList | null) {
    if (!files?.length) return
    setError(null)
    setUploading(true)
    try {
      for (const file of Array.from(files)) {
        const form = new FormData()
        form.append('file', file)
        const uploaded = await api.upload<Upload>('/files/upload', form)
        setUploads((prev) => [...prev, uploaded])
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось загрузить файл')
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  return (
    <div className="shrink-0 border-t border-line bg-surface px-3 pb-safe pt-2">
      {error && (
        <div className="pb-2">
          <InlineError message={error} />
        </div>
      )}

      {uploads.length > 0 && (
        <ul className="flex flex-wrap gap-2 pb-2">
          {uploads.map((file) => (
            <li
              key={file.upload_key}
              className="flex items-center gap-2 rounded bg-surface-raised px-2.5 py-1.5"
            >
              <span className="max-w-[160px] truncate text-xs text-ink">{file.file_name}</span>
              <span className="text-micro text-ink-faint">{fileSize(file.size_bytes)}</span>
              <button
                aria-label={`Убрать ${file.file_name}`}
                onClick={() => setUploads((prev) => prev.filter((u) => u.upload_key !== file.upload_key))}
                className="text-ink-faint transition-colors hover:text-danger"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      {internal && (
        <div className="mb-2 flex items-center gap-1.5 text-micro text-warning">
          <Lock className="size-3.5" aria-hidden />
          Служебная заметка — клиент её не увидит
        </div>
      )}

      <div className="flex items-end gap-1.5">
        <input
          ref={fileInput}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => void onPickFiles(e.target.files)}
        />
        <IconButton
          label="Прикрепить файл"
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
        >
          <Paperclip className="size-5" aria-hidden />
        </IconButton>

        {/* ТЗ п. 3.1: шаблоны вне первой версии. Кнопка скрыта, а справочник,
            запрос и само окно остались — вернуть их будет сменой этого флага. */}
        {TEMPLATES_ENABLED && templates && templates.length > 0 && (
          <IconButton label="Шаблоны" onClick={() => setTemplatesOpen(true)}>
            <StickyNote className="size-5" aria-hidden />
          </IconButton>
        )}

        <IconButton
          label={internal ? 'Обычное сообщение' : 'Служебная заметка'}
          active={internal}
          onClick={() => setInternal((v) => !v)}
        >
          <Lock className="size-5" aria-hidden />
        </IconButton>

        <Textarea
          ref={textarea}
          rows={1}
          value={text}
          maxLength={MAX_LENGTH}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={internal ? 'Заметка для команды…' : 'Написать сообщение…'}
          className={cn(
            'max-h-40 min-h-10 flex-1 py-2.5',
            internal && 'border-warning/50 bg-warning-soft/40',
          )}
        />

        <Button
          aria-label="Отправить"
          onClick={() => void submit()}
          disabled={!canSend}
          loading={send.isPending || uploading}
          className="size-10 rounded-full p-0"
        >
          {!(send.isPending || uploading) && <Send className="size-4" aria-hidden />}
        </Button>
      </div>

      <Sheet
        open={templatesOpen}
        onOpenChange={setTemplatesOpen}
        title="Шаблоны"
        description="Текст добавится к тому, что уже набрано"
      >
        <ul className="flex flex-col gap-2">
          {templates?.map((template) => (
            <li key={template.id}>
              <button
                onClick={() => {
                  setText((prev) => (prev ? `${prev}\n${template.text}` : template.text))
                  setTemplatesOpen(false)
                  textarea.current?.focus()
                }}
                className="flex w-full flex-col gap-1 rounded-md bg-surface-raised px-3 py-2.5 text-left transition-colors hover:bg-line"
              >
                <span className="text-sm font-medium text-ink">{template.title}</span>
                <span className="line-clamp-2 text-xs text-ink-muted">{template.text}</span>
              </button>
            </li>
          ))}
        </ul>
      </Sheet>
    </div>
  )
}
