import { Lock, Mic, Paperclip, Send, StickyNote } from 'lucide-react'
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
} from 'react'

import { AttachmentTray } from '@/features/chats/components/composer/AttachmentTray'
import { VoiceRecorderBar } from '@/features/chats/components/composer/VoiceRecorderBar'
import { decidePaste } from '@/features/chats/components/composer/clipboard'
import { useAttachmentQueue } from '@/features/chats/components/composer/useAttachmentQueue'
import {
  isRecordingSupported,
  useVoiceRecorder,
} from '@/features/chats/components/composer/useVoiceRecorder'
import { useSendMessage, useTemplates } from '@/features/chats/queries'
import { ApiError } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { Button, IconButton, InlineError, Sheet, Textarea } from '@/shared/ui'

const MAX_LENGTH = 4096

// Шаблоны сообщений выключены до второй версии (ТЗ от 01.09.2026, п. 3.1).
const TEMPLATES_ENABLED = false

export interface ComposerHandle {
  /** Файлы, брошенные на окно чата. */
  addFiles: (files: File[]) => void
}

/**
 * Поле ввода чата: текст, вложения, голосовое.
 *
 * Вложения появляются тремя путями — скрепка, вставка из буфера (Ctrl/Cmd+V)
 * и перетаскивание файлов на окно чата — и сразу начинают загружаться.
 * Голосовое записывается прямо здесь: микрофон → запись → в список вложений
 * с плеером → «Отправить». Клиенту оно придёт настоящим голосовым Telegram.
 */
export const Composer = forwardRef<ComposerHandle, { conversationId: number }>(function Composer(
  { conversationId },
  ref,
) {
  const send = useSendMessage(conversationId)
  const { data: templates } = useTemplates()
  const queue = useAttachmentQueue()
  // Запись остановлена — голосовое уходит в список вложений и сразу грузится.
  const recorder = useVoiceRecorder((blob, seconds, sendNow) => {
    queue.addVoice(blob, seconds)
    if (sendNow) setAutoSend(true)
  })
  const fileInput = useRef<HTMLInputElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)

  const [text, setText] = useState('')
  const [internal, setInternal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [templatesOpen, setTemplatesOpen] = useState(false)
  // Голосовое, которое нужно отправить, как только оно загрузится на сервер.
  const [autoSend, setAutoSend] = useState(false)

  const recording = recorder.state === 'recording' || recorder.state === 'requesting'
  const hasContent = text.trim().length > 0 || queue.items.length > 0
  const canSend = hasContent && !send.isPending && !queue.uploading && !queue.failed && !recording
  const canRecord = isRecordingSupported()

  function addFiles(files: File[]) {
    if (!files.length) return
    setError(queue.add(files))
  }

  useImperativeHandle(ref, () => ({ addFiles }))

  async function submit() {
    if (!canSend) {
      if (queue.failed) setError('Один из файлов не загрузился — повторите загрузку или уберите его')
      return
    }
    setError(null)
    try {
      await send.mutateAsync({
        text: text.trim() || undefined,
        is_internal: internal,
        uploads: queue.uploads,
      })
      setText('')
      queue.clear()
      setInternal(false)
      textarea.current?.focus()
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось отправить')
    }
  }

  // «Отправить» из полосы записи: ждём конца загрузки и отправляем. Если файл
  // не загрузился, остаётся в списке с причиной — менеджер решает сам.
  useEffect(() => {
    if (!autoSend || queue.uploading || send.isPending) return
    setAutoSend(false)
    if (queue.items.length > 0 && !queue.failed) void submit()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSend, queue.uploading, queue.failed, queue.items.length])

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submit()
    }
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const decision = decidePaste(event.clipboardData)
    if (decision.files.length === 0) return // браузер вставит текст сам
    event.preventDefault()
    addFiles(decision.files)
  }

  return (
    <div className="shrink-0 border-t border-line bg-surface px-3 pb-safe pt-2">
      {(error || recorder.error) && (
        <div className="pb-2">
          <InlineError message={error ?? recorder.error ?? ''} />
        </div>
      )}

      <AttachmentTray items={queue.items} onRemove={queue.remove} onRetry={queue.retry} />

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
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />

        {recording ? (
          <VoiceRecorderBar
            seconds={recorder.seconds}
            level={recorder.level}
            requesting={recorder.state === 'requesting'}
            onCancel={recorder.cancel}
            onStop={recorder.stop}
            onSend={recorder.stopAndSend}
          />
        ) : (
          <>
            <IconButton label="Прикрепить файл" onClick={() => fileInput.current?.click()}>
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
              onPaste={onPaste}
              placeholder={internal ? 'Заметка для команды…' : 'Написать сообщение…'}
              className={cn(
                'max-h-40 min-h-10 flex-1 py-2.5',
                internal && 'border-warning/50 bg-warning-soft/40',
              )}
            />
          </>
        )}

        {/* Как в Telegram: пусто — микрофон, есть что отправить — стрелка. */}
        {!hasContent && canRecord && !recording ? (
          <Button
            aria-label="Записать голосовое"
            title="Записать голосовое"
            onClick={() => void recorder.start()}
            className="size-10 rounded-full p-0"
          >
            <Mic className="size-4" aria-hidden />
          </Button>
        ) : (
          !recording && (
            <Button
              aria-label="Отправить"
              onClick={() => void submit()}
              disabled={!canSend}
              loading={send.isPending || queue.uploading}
              className="size-10 rounded-full p-0"
            >
              {!(send.isPending || queue.uploading) && <Send className="size-4" aria-hidden />}
            </Button>
          )
        )}
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
})
