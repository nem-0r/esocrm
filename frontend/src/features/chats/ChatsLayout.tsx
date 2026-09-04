import { AlertTriangle, ChevronRight, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { useMe } from '@/shared/hooks/useAuth'
import { cn } from '@/shared/lib/cn'
import { plural } from '@/shared/lib/format'
import {
  Button,
  Dot,
  EmptyState,
  ErrorState,
  Input,
  ListSkeleton,
  Segmented,
} from '@/shared/ui'
import { SectionActions } from '@/app/layout/SectionActions'
import { ChatListItem } from '@/features/chats/components/ChatListItem'
import { useConversations, useCounters, useNextConversation, type ChatFilter } from '@/features/chats/queries'

export function ChatsLayout({
  selectedId,
  children,
}: {
  selectedId: number | null
  children?: React.ReactNode
}) {
  const me = useMe()
  const navigate = useNavigate()
  const [filter, setFilter] = useState<ChatFilter>('all')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

  const { data: counters } = useCounters()
  const list = useConversations({ filter, q: debounced })
  const takeNext = useNextConversation()

  const conversations = useMemo(
    () => list.data?.pages.flatMap((page) => page.items) ?? [],
    [list.data],
  )


  async function takeFirst() {
    try {
      const conversation = await takeNext.mutateAsync()
      navigate(`/chats/${conversation.id}`)
    } catch {
      // Пустая очередь — сервер отвечает 404, состояние ниже это показывает
    }
  }

  return (
    <div className="flex min-h-0 flex-1">
      {/* Список чатов. На мобильном виден, только когда чат не открыт. */}
      <section
        className={cn(
          'flex min-h-0 w-full flex-col border-line desk:w-[300px] desk:shrink-0 desk:border-r xl:w-[340px]',
          selectedId !== null && 'hidden desk:flex',
        )}
      >
        <header className="shrink-0 border-b border-line px-4 pb-3 pt-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-1">
              <h1 className="truncate text-lg font-semibold text-ink">
                Здравствуйте, {me.full_name.split(' ')[0]}
              </h1>
              {/* ТЗ п. 1.1: здесь только статус, без переключателя. Менять его
                  можно в профиле — чтобы случайным касанием не отключить приём
                  заявок посреди рабочего дня. */}
              <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                <Dot tone={me.accepting_leads ? 'success' : 'neutral'} />
                {me.accepting_leads ? 'Приём заявок включён' : 'Приём заявок выключен'}
              </span>
            </div>
            <SectionActions />
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2">
            <Metric value={counters?.awaiting ?? 0} label="ждут ответа" tone="danger" />
            <Metric value={counters?.total ?? 0} label="чатов всего" />
          </div>

          <Button
            fullWidth
            className="mt-3"
            onClick={() => void takeFirst()}
            loading={takeNext.isPending}
            disabled={(counters?.total ?? 0) === 0}
          >
            <ChevronRight className="size-4" aria-hidden />
            Взять первого в очереди
          </Button>

          <Input
            className="mt-3"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Поиск по чатам и клиентам"
            leading={<Search className="size-4" aria-hidden />}
          />

          <Segmented
            className="mt-3"
            value={filter}
            onChange={setFilter}
            options={[
              { value: 'all', label: 'Все', count: counters?.total },
              { value: 'awaiting', label: 'Ждут ответа', count: counters?.awaiting, tone: 'danger' },
              {
                value: 'awaiting_payment',
                label: 'Ожидают оплаты',
                count: counters?.awaiting_payment,
              },
            ]}
          />
        </header>

        {/* Баннер про долгое ожидание — это главная дисциплина продукта */}
        {(counters?.over_threshold ?? 0) > 0 && filter !== 'awaiting' && (
          <button
            onClick={() => setFilter('awaiting')}
            className="flex shrink-0 items-center gap-2 border-b border-danger/30 bg-danger-soft px-4 py-2.5 text-left text-sm text-danger transition-colors hover:bg-danger/20"
          >
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            <span className="flex-1">
              {counters!.over_threshold}{' '}
              {plural(counters!.over_threshold, 'клиент ждёт', 'клиента ждут', 'клиентов ждут')}{' '}
              больше {counters!.over_threshold_minutes}{' '}
              {plural(counters!.over_threshold_minutes, 'минуты', 'минут', 'минут')}
            </span>
            <ChevronRight className="size-4 shrink-0" aria-hidden />
          </button>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {list.isLoading ? (
            <ListSkeleton rows={8} />
          ) : list.error ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : conversations.length === 0 ? (
            <EmptyState
              title={debounced ? `По запросу «${debounced}» ничего не найдено` : 'Чатов пока нет'}
              hint={
                debounced
                  ? 'Проверьте написание или очистите поиск.'
                  : filter === 'awaiting'
                    ? 'Все клиенты получили ответ.'
                    : 'Как только клиент напишет, диалог появится здесь.'
              }
            />
          ) : (
            <>
              {conversations.map((conversation) => (
                <ChatListItem
                  key={conversation.id}
                  conversation={conversation}
                  active={conversation.id === selectedId}
                />
              ))}
              {list.hasNextPage && (
                <div className="p-3">
                  <Button
                    variant="secondary"
                    fullWidth
                    loading={list.isFetchingNextPage}
                    onClick={() => void list.fetchNextPage()}
                  >
                    Показать ещё
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </section>

      {/* Область чата */}
      <section
        className={cn(
          'flex min-h-0 min-w-0 flex-1 flex-col',
          selectedId === null && 'hidden desk:flex',
        )}
      >
        {children ?? (
          <EmptyState
            title="Выберите чат"
            hint="Слева список диалогов. Или нажмите «Взять первого в очереди»."
          />
        )}
      </section>
    </div>
  )
}

function Metric({
  value,
  label,
  tone,
}: {
  value: number
  label: string
  tone?: 'danger'
}) {
  return (
    <div className="flex flex-col items-center rounded-md bg-surface py-2">
      <span
        className={cn(
          'tnum text-xl font-semibold',
          tone === 'danger' && value > 0 ? 'text-danger' : 'text-ink',
        )}
      >
        {value}
      </span>
      <span className="text-micro text-ink-muted">{label}</span>
    </div>
  )
}
