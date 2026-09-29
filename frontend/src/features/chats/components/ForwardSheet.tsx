import { Ban, Check, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { Conversation } from '@/entities/types'
import { FUNNEL_LABEL } from '@/features/profile/lib'
import { useConversations, useForwardMessages } from '@/features/chats/queries'
import { ApiError } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { plural } from '@/shared/lib/format'
import {
  Button,
  EmptyState,
  Field,
  InlineError,
  Input,
  ListSkeleton,
  Sheet,
  Switch,
  Textarea,
  toast,
} from '@/shared/ui'

// Столько же разрешает бэкенд (MAX_FORWARD_MESSAGES в schemas/message.py) —
// проверяем заранее, чтобы не ловить техническую ошибку сервера после отправки.
// Экспортирован — пригодится, например, для счётчика в панели выбора ChatPage.
export const MAX_FORWARD_MESSAGES = 50

/**
 * Переслать выбранные сообщения в другой чат CRM.
 *
 * Как именно уйдёт, решает сервер: внутри одного аккаунта Telegram — настоящая
 * пересылка (альбомы остаются альбомами), между аккаунтами — копия. Подпись
 * «Переслано от …» по умолчанию скрыта: клиент Б не должен видеть имя клиента А.
 */
export function ForwardSheet({
  open,
  onOpenChange,
  sourceConversationId,
  sourceAccountId,
  messageIds,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  sourceConversationId: number
  sourceAccountId: number
  messageIds: number[]
  onDone: () => void
}) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [target, setTarget] = useState<Conversation | null>(null)
  const [hideSender, setHideSender] = useState(true)
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  const conversations = useConversations({ filter: 'all', q: debounced })
  const forward = useForwardMessages()

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    if (!open) {
      setQuery('')
      setDebounced('')
      setTarget(null)
      setHideSender(true)
      setComment('')
      setError(null)
    }
  }, [open])

  const rows = useMemo(
    () => conversations.data?.pages.flatMap((page) => page.items) ?? [],
    [conversations.data],
  )
  const count = messageIds.length
  const tooMany = count > MAX_FORWARD_MESSAGES
  const sameAccount = target ? target.account.id === sourceAccountId : null

  async function submit() {
    if (!target || tooMany) return
    setError(null)
    try {
      await forward.mutateAsync({
        targetId: target.id,
        source_conversation_id: sourceConversationId,
        message_ids: messageIds,
        hide_sender: hideSender,
        comment: comment.trim() || undefined,
      })
      toast(`Переслано в чат «${target.client.name}»`, {
        to: `/chats/${target.id}`,
        label: 'Открыть',
      })
      onDone()
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось переслать')
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={`Переслать ${count} ${plural(count, 'сообщение', 'сообщения', 'сообщений')}`}
      description="Выберите чат, куда переслать"
      footer={
        <div className="flex flex-col gap-2">
          {tooMany && (
            <InlineError
              message={`За один раз можно переслать до 50 сообщений — выбрано ${count}`}
            />
          )}
          {error && <InlineError message={error} />}
          <Button
            fullWidth
            disabled={!target || tooMany}
            loading={forward.isPending}
            onClick={() => void submit()}
          >
            {target ? `Переслать в «${target.client.name}»` : 'Выберите чат'}
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <Input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Имя клиента, ID или текст переписки"
          leading={<Search className="size-4" aria-hidden />}
        />

        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
          {conversations.isLoading ? (
            <ListSkeleton rows={4} />
          ) : conversations.error ? (
            <InlineError message="Не удалось загрузить чаты" onRetry={() => void conversations.refetch()} />
          ) : rows.length === 0 ? (
            <EmptyState title="Чатов не найдено" />
          ) : (
            rows.map((row) => {
              const chosen = target?.id === row.id
              const blocked = row.is_blocked_by_client
              return (
                <button
                  key={row.id}
                  type="button"
                  disabled={blocked}
                  onClick={() => setTarget(row)}
                  className={cn(
                    'flex items-center gap-3 rounded-md px-3 py-2 text-left transition-colors',
                    blocked
                      ? 'cursor-not-allowed opacity-60'
                      : chosen
                        ? 'bg-accent-soft'
                        : 'hover:bg-surface-raised',
                  )}
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm text-ink">
                      {row.client.name}
                      {row.id === sourceConversationId ? ' · этот чат' : ''}
                    </span>
                    <span className="truncate text-micro text-ink-faint">
                      {row.account.title} · {FUNNEL_LABEL[row.account.funnel_stage]} · id {row.client.id}
                    </span>
                    {blocked && (
                      <span className="flex items-center gap-1 text-micro text-warning">
                        <Ban className="size-3 shrink-0" aria-hidden />
                        Клиент заблокировал номер — переслать сюда нельзя
                      </span>
                    )}
                  </span>
                  {chosen && <Check className="size-4 shrink-0 text-accent-text" aria-hidden />}
                </button>
              )
            })
          )}
          {conversations.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              loading={conversations.isFetchingNextPage}
              onClick={() => void conversations.fetchNextPage()}
            >
              Показать ещё
            </Button>
          )}
        </div>

        {sameAccount === false ? (
          <p className="text-xs leading-relaxed text-ink-faint">
            Чат на другом аккаунте («{target?.account.title}»): уйдёт копия — клиент увидит
            обычные сообщения без подписи «Переслано».
          </p>
        ) : (
          <Switch
            checked={hideSender}
            onChange={setHideSender}
            label="Не показывать, от кого переслано"
            hint={
              hideSender
                ? 'Клиент увидит сообщения как новые'
                : 'Клиент увидит «Переслано от …» с именем отправителя'
            }
          />
        )}

        <Field label="Комментарий" hint="Необязательно. Уйдёт отдельным сообщением перед пересланными">
          <Textarea
            rows={2}
            value={comment}
            maxLength={4096}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Например: «Вот пример разбора, о котором говорили»"
          />
        </Field>
      </div>
    </Sheet>
  )
}
