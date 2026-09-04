import { ArrowLeft, CreditCard, IdCard, Plus, UserCheck } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { realtime } from '@/shared/api/ws'
import { dateFull, dayDivider, dayKey, money, plural, waitingLabel } from '@/shared/lib/format'
import { Badge, Button, EmptyState, ErrorState, ListSkeleton } from '@/shared/ui'
import { ChatsLayout } from '@/features/chats/ChatsLayout'
import { Composer } from '@/features/chats/components/Composer'
import { MessageBubble } from '@/features/chats/components/MessageBubble'
import { TransferSheet } from '@/features/chats/components/TransferSheet'
import { DealSheet } from '@/features/deals/DealSheet'
import { FUNNEL_LABEL } from '@/features/profile/lib'
import {
  useConversation,
  useMarkRead,
  useMessages,
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
  const navigate = useNavigate()
  const conversation = useConversation(conversationId)
  const messages = useMessages(conversationId)
  const markRead = useMarkRead()
  const bottom = useRef<HTMLDivElement>(null)
  const [dealOpen, setDealOpen] = useState(false)
  const [transferOpen, setTransferOpen] = useState(false)

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

  return (
    <>
      <header className="shrink-0 border-b border-line bg-surface px-3 py-2.5">
        <div className="flex items-center gap-2">
          <button
            aria-label="Назад к чатам"
            onClick={() => navigate('/chats')}
            className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink desk:hidden"
          >
            <ArrowLeft className="size-5" aria-hidden />
          </button>

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
                  <MessageBubble message={message} />
                </div>
              )
            })}
            <div ref={bottom} />
          </div>
        )}
      </div>

      <Composer conversationId={conversationId} />

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
    </>
  )
}
