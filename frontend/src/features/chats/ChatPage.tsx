import { AlertTriangle, Copy, CreditCard, Forward, IdCard, Paperclip, Plus, UserCheck, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Link, useParams } from 'react-router-dom'

import { realtime } from '@/shared/api/ws'
import { dateFull, dayDivider, dayKey, money, plural, waitingLabel } from '@/shared/lib/format'
import { BackButton, Badge, Button, EmptyState, ErrorState, ListSkeleton, toast, toastError } from '@/shared/ui'
import { ChatsLayout } from '@/features/chats/ChatsLayout'
import { Composer, type ComposerHandle } from '@/features/chats/components/Composer'
import { ForwardSheet } from '@/features/chats/components/ForwardSheet'
import { MessageBubble, canForward } from '@/features/chats/components/MessageBubble'
import { TransferSheet } from '@/features/chats/components/TransferSheet'
import { copyText, formatForCopy } from '@/features/chats/components/message/copy'
import { DealSheet } from '@/features/deals/DealSheet'
import { FUNNEL_LABEL } from '@/features/profile/lib'
import {
  useConversation,
  useEditMessage,
  useMarkRead,
  useMessages,
  useRetryMessage,
} from '@/features/chats/queries'

export function ChatPage() {
  const { conversationId } = useParams<{ conversationId: string }>()
  const id = Number(conversationId)

  if (!Number.isFinite(id)) {
    return (
      <ChatsLayout selectedId={null}>
        <EmptyState title="Чат не найден" />
      </ChatsLayout>
    )
  }

  return (
    <ChatsLayout selectedId={id}>
      <ChatPane conversationId={id} />
    </ChatsLayout>
  )
}

function ChatPane({ conversationId }: { conversationId: number }) {
  const conversation = useConversation(conversationId)
  const messages = useMessages(conversationId)
  const markRead = useMarkRead()
  const retryMessage = useRetryMessage(conversationId)
  const editMessage = useEditMessage(conversationId)
  const bottom = useRef<HTMLDivElement>(null)
  const composer = useRef<ComposerHandle>(null)
  const [dealOpen, setDealOpen] = useState(false)
  const [transferOpen, setTransferOpen] = useState(false)
  // Режим выбора сообщений: переслать или скопировать несколько разом.
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [forwardIds, setForwardIds] = useState<number[] | null>(null)
  const [dragging, setDragging] = useState(false)
  const dragDepth = useRef(0)

  // Другой чат — выбор из прошлого чата не должен «переехать» сюда.
  useEffect(() => {
    setSelecting(false)
    setSelected([])
    setForwardIds(null)
  }, [conversationId])

  // Открыли чат — сбрасываем непрочитанные и сообщаем, что мы здесь.
  useEffect(() => {
    markRead.mutate(conversationId)
    realtime.setViewing(conversationId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])

  const items = useMemo(
    // Сервер отдаёт от новых к старым — в ленте порядок обратный.
    () => (messages.data?.pages.flatMap((page) => page.items) ?? []).slice().reverse(),
    [messages.data],
  )

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [items.length])

  if (conversation.isLoading) return <ListSkeleton rows={6} />
  if (conversation.error) {
    return <ErrorState error={conversation.error} onRetry={() => void conversation.refetch()} />
  }
  if (!conversation.data) return <EmptyState title="Чат не найден" />

  const chat = conversation.data
  const paidCount = chat.client_paid_count ?? 0

  function toggle(id: number) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((value) => value !== id) : [...prev, id]))
  }

  function startSelecting(id: number) {
    setSelecting(true)
    setSelected([id])
  }

  function stopSelecting() {
    setSelecting(false)
    setSelected([])
  }

  // Выбранное — в порядке ленты, а не в порядке нажатий: так и пересылается.
  const chosen = items.filter((message) => selected.includes(message.id))
  const forwardable = chosen.filter(canForward)

  function copySelected() {
    void copyText(formatForCopy(chosen, chat.client.name)).then(
      () => {
        toast(`Скопировано: ${chosen.length} ${plural(chosen.length, 'сообщение', 'сообщения', 'сообщений')}`)
        stopSelecting()
      },
      () => toastError('Не удалось скопировать'),
    )
  }

  function onDragEnter(event: DragEvent<HTMLDivElement>) {
    if (!Array.from(event.dataTransfer.types).includes('Files')) return
    event.preventDefault()
    dragDepth.current += 1
    setDragging(true)
  }

  function onDragLeave() {
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragging(false)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    dragDepth.current = 0
    setDragging(false)
    composer.current?.addFiles(Array.from(event.dataTransfer.files))
  }

  return (
    <div
      className="relative flex min-h-0 flex-1 flex-col"
      onDragEnter={onDragEnter}
      onDragOver={(event) => {
        if (Array.from(event.dataTransfer.types).includes('Files')) event.preventDefault()
      }}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-2 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-accent bg-bg/85">
          <span className="flex items-center gap-2 text-sm text-ink">
            <Paperclip className="size-5 text-accent-text" aria-hidden />
            Отпустите, чтобы прикрепить к сообщению
          </span>
        </div>
      )}

      {selecting && (
        <div className="flex shrink-0 items-center gap-2 border-b border-line bg-surface px-3 py-2.5">
          <button
            type="button"
            aria-label="Отменить выбор"
            onClick={stopSelecting}
            className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
          >
            <X className="size-5" aria-hidden />
          </button>
          <span className="flex-1 text-sm text-ink">Выбрано: {chosen.length}</span>
          <Button size="sm" variant="secondary" disabled={chosen.length === 0} onClick={copySelected}>
            <Copy className="size-4" aria-hidden />
            Копировать
          </Button>
          <Button
            size="sm"
            disabled={forwardable.length === 0}
            onClick={() => setForwardIds(forwardable.map((message) => message.id))}
          >
            <Forward className="size-4" aria-hidden />
            Переслать
          </Button>
        </div>
      )}

      <header className={selecting ? 'hidden' : 'shrink-0 border-b border-line bg-surface px-3 py-2.5'}>
        <div className="flex items-center gap-2">
          <BackButton fallback="/chats" label="Назад к чатам" />

          {/* Имя занимает всю строку: бейдж ожидания рядом с ним ломал верстку
              на телефоне и обрезал имя до пары букв. */}
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="flex min-w-0 items-baseline gap-2">
              <span className="truncate text-sm font-semibold text-ink">{chat.client.name}</span>
              <span className="tnum shrink-0 text-micro text-ink-faint">id {chat.client.id}</span>
            </div>
            <div className="flex min-w-0 items-center gap-1.5">
              {chat.awaiting_minutes !== null && (
                <Badge tone="danger" className="shrink-0 whitespace-nowrap">
                  ждёт {waitingLabel(chat.awaiting_minutes)}
                </Badge>
              )}
              {/* ТЗ п. 2.2: порядок и состав строки заданы заказчиком —
                  аккаунт | направление | оплаты | ответственный | срок клиента.
                  Оплаты считаются только по этому аккаунту (п. 2.3). */}
              <span className="truncate text-micro text-ink-faint">
                {[
                  chat.account.title,
                  FUNNEL_LABEL[chat.account.funnel_stage],
                  paidCount > 0
                    ? `оплачено ${money(chat.client_paid_amount ?? 0)} · ${paidCount} ${plural(paidCount, 'сделка', 'сделки', 'сделок')}`
                    : 'оплат пока нет',
                  chat.responsible ? `ведёт ${chat.responsible.full_name}` : 'без ответственного',
                  chat.client.first_contact_at
                    ? `клиент с ${dateFull(chat.client.first_contact_at)}`
                    : null,
                ]
                  .filter(Boolean)
                  .join(' | ')}
              </span>
            </div>
          </div>

          <div className="flex shrink-0 gap-1">
            <Button size="sm" variant="secondary" onClick={() => setDealOpen(true)}>
              <Plus className="size-4" aria-hidden />
              Оплата
            </Button>
            <button
              aria-label="Передать диалог"
              title="Передать диалог"
              onClick={() => setTransferOpen(true)}
              className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
            >
              <UserCheck className="size-5" aria-hidden />
            </button>
            <Link
              to={`/clients/${chat.client.id}`}
              aria-label="Карточка клиента"
              title="Карточка клиента"
              className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
            >
              <IdCard className="size-5" aria-hidden />
            </Link>
          </div>
        </div>

        {chat.is_blocked_by_client && (
          <div className="mt-2 flex items-center gap-2 rounded-md bg-danger-soft px-3 py-2 text-sm text-danger">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            <span className="flex-1">
              Клиент заблокировал этот номер — сообщения не доходят
            </span>
          </div>
        )}

        {chat.has_awaiting_deal && (
          <Link
            // ТЗ п. 4.6: из чата — только оплаты этого канала.
            to={`/payments?client_id=${chat.client.id}&account_id=${chat.account.id}&status=awaiting`}
            className="mt-2 flex items-center gap-2 rounded-md bg-accent-soft px-3 py-2 text-sm text-accent-text transition-colors hover:bg-accent/25"
          >
            <CreditCard className="size-4 shrink-0" aria-hidden />
            <span className="flex-1">
              Ждёт оплаты · {money(chat.awaiting_deal_amount ?? 0)}
            </span>
            <span className="text-micro text-ink-faint">открыть сделки</span>
          </Link>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto bg-surface-sunken py-3">
        {messages.isLoading ? (
          <ListSkeleton rows={5} />
        ) : messages.error ? (
          <ErrorState error={messages.error} onRetry={() => void messages.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState title="Сообщений пока нет" hint="Напишите первым — поле ввода ниже." />
        ) : (
          <div className="flex flex-col gap-2">
            {messages.hasNextPage && (
              <div className="px-4 pb-2">
                <Button
                  variant="ghost"
                  size="sm"
                  fullWidth
                  loading={messages.isFetchingNextPage}
                  onClick={() => void messages.fetchNextPage()}
                >
                  Показать более раннее
                </Button>
              </div>
            )}
            {items.map((message, index) => {
              const previous = index > 0 ? items[index - 1] : null
              const startsDay =
                previous === null || dayKey(previous.created_at) !== dayKey(message.created_at)
              return (
                <div key={message.id} className="flex flex-col gap-2">
                  {startsDay && (
                    <div className="flex justify-center py-1">
                      <span className="rounded-full bg-surface px-2.5 py-1 text-micro text-ink-faint">
                        {dayDivider(message.created_at)}
                      </span>
                    </div>
                  )}
                  <MessageBubble
                    message={message}
                    onRetry={() => retryMessage.mutate(message.id)}
                    retrying={retryMessage.isPending && retryMessage.variables === message.id}
                    onEdit={(text) => editMessage.mutateAsync({ messageId: message.id, text })}
                    savingEdit={
                      editMessage.isPending && editMessage.variables?.messageId === message.id
                    }
                    onForward={() => setForwardIds([message.id])}
                    selection={{
                      active: selecting,
                      selected: selected.includes(message.id),
                      onToggle: () => toggle(message.id),
                      onStart: () => startSelecting(message.id),
                    }}
                  />
                </div>
              )
            })}
            <div ref={bottom} />
          </div>
        )}
      </div>

      {/* Ключ по чату: черновик, вложения и запись голосового принадлежат
          своему чату — переключились на другого клиента, и незаконченное
          сообщение не уедет не тому человеку. */}
      <Composer key={conversationId} ref={composer} conversationId={conversationId} />

      <ForwardSheet
        open={forwardIds !== null}
        onOpenChange={(open) => !open && setForwardIds(null)}
        sourceConversationId={conversationId}
        sourceAccountId={chat.account.id}
        messageIds={forwardIds ?? []}
        onDone={stopSelecting}
      />

      <TransferSheet
        open={transferOpen}
        onOpenChange={setTransferOpen}
        conversation={chat}
      />

      <DealSheet
        open={dealOpen}
        onOpenChange={setDealOpen}
        conversationId={conversationId}
        clientName={chat.client.name}
      />
    </div>
  )
}
