import { AlertTriangle, ChevronRight, Search, X } from 'lucide-react'
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
  toast,
  toastError,
} from '@/shared/ui'
import { ApiError } from '@/shared/api/client'
import { SectionActions } from '@/app/layout/SectionActions'
import { ChatListItem } from '@/features/chats/components/ChatListItem'
import { useConversations, useCounters, useNextConversation, type ChatFilter } from '@/features/chats/queries'
import {
  QUEUE_DONE,
  clearTaken,
  loadFilter,
  loadTaken,
  overFilterLabel,
  pushTaken,
  saveFilter,
  saveTaken,
} from '@/features/chats/replyQueue'

export function ChatsLayout({
  selectedId,
  children,
}: {
  selectedId: number | null
  children?: React.ReactNode
}) {
  const me = useMe()
  const navigate = useNavigate()
  // Фильтр помним в сессии: из пустого списка в открытый чат раздел пересобирается
  // (другой маршрут), и без этого выбранный фильтр сбрасывался бы после первого клика.
  const [filter, setFilterState] = useState<ChatFilter>(() => loadFilter())
  const setFilter = (next: ChatFilter) => {
    setFilterState(next)
    saveFilter(next)
  }
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

  // Сколько строк в этом списке всего — по счётчикам сервера, а не по длине загруженной
  // страницы: число на баннере и в чипе должно совпадать с тем, что можно долистать.
  const listTotal =
    debounced || !counters
      ? null
      : filter === 'awaiting_over'
        ? counters.over_threshold
        : filter === 'awaiting'
          ? counters.awaiting
          : filter === 'awaiting_payment'
            ? counters.awaiting_payment
            : counters.total

  // «Взять первого»: уже взятые в этой сессии чаты уходят серверу в `skip`, поэтому
  // повторное нажатие ведёт к следующему, а не к тому же самому чату.
  async function takeFirst() {
    const taken = loadTaken(me.id)
    try {
      const conversation = await takeNext.mutateAsync(taken)
      saveTaken(me.id, pushTaken(taken, conversation.id))
      navigate(`/chats/${conversation.id}`)
    } catch (error) {
      if (error instanceof ApiError && error.code === QUEUE_DONE) {
        // Все ждущие уже взяты: говорим об этом и начинаем круг заново
        clearTaken(me.id)
        toast('Больше нет чатов в очереди. Нажмите ещё раз — начнём с самого давнего.')
      } else if (error instanceof ApiError && error.isNotFound) {
        toast(error.message)
      } else {
        toastError(error instanceof Error ? error.message : 'Не удалось взять чат из очереди')
      }
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

          {/* Фильтр «Ждут >N мин» включается баннером ниже; отдельного таба у него нет,
              поэтому, пока он активен, виден выбранным чипом, который снимается крестиком. */}
          {filter === 'awaiting_over' && (
            <button
              type="button"
              onClick={() => setFilter('all')}
              aria-label={`Снять фильтр «${overFilterLabel(counters?.over_threshold_minutes)}»`}
              className="mt-2 inline-flex items-center gap-1.5 rounded bg-accent-soft px-3 py-1.5 text-sm text-accent-text transition-colors hover:bg-accent-soft/70"
            >
              {overFilterLabel(counters?.over_threshold_minutes)}
              {(counters?.over_threshold ?? 0) > 0 && (
                <span className="tnum text-micro font-semibold text-danger">
                  {counters!.over_threshold}
                </span>
              )}
              <X className="size-3.5" aria-hidden />
            </button>
          )}
        </header>

        {/* Баннер про долгое ожидание — это главная дисциплина продукта */}
        {(counters?.over_threshold ?? 0) > 0 && filter !== 'awaiting_over' && (
          <button
            onClick={() => setFilter('awaiting_over')}
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
              title={
                debounced
                  ? `По запросу «${debounced}» ничего не найдено`
                  : filter === 'awaiting_over'
                    ? 'Долгих ожиданий нет'
                    : 'Чатов пока нет'
              }
              hint={
                debounced
                  ? 'Проверьте написание или очистите поиск.'
                  : filter === 'awaiting_over'
                    ? `Никто не ждёт ответа дольше ${counters?.over_threshold_minutes ?? ''} мин.`
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
                  {listTotal !== null && listTotal > conversations.length && (
                    <p className="mb-2 text-center text-micro text-ink-faint">
                      Показано {conversations.length} из {listTotal}
                    </p>
                  )}
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
