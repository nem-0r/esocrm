import { CreditCard, FileText, MessageSquare, Search, User, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { dateShort, money } from '@/shared/lib/format'
import { cn } from '@/shared/lib/cn'
import { useMe } from '@/shared/hooks/useAuth'
import {
  Avatar,
  BackButton,
  Badge,
  EmptyState,
  ErrorState,
  Input,
  ListSkeleton,
  SectionTitle,
} from '@/shared/ui'
import { useConversations } from '@/features/chats/queries'
import { DEAL_STATUS_LABEL, DEAL_STATUS_TONE } from '@/features/deals/lib'
import {
  MIN_QUERY,
  useClearHistory,
  useSearch,
  useSearchHistory,
  type SearchType,
} from '@/features/search/queries'

const FILTERS: { value: SearchType; label: string }[] = [
  { value: 'clients', label: 'Клиенты' },
  { value: 'deals', label: 'Оплаты' },
  { value: 'chats', label: 'Чаты' },
  { value: 'files', label: 'Файлы' },
  { value: 'managers', label: 'Менеджеры' },
]

export function SearchPage() {
  const me = useMe()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [types, setTypes] = useState<SearchType[]>([])
  // ТЗ Б.13: поиск по менеджерам виден только руководителю — у менеджера
  // этот фильтр всегда вернул бы пусто, показывать его нет смысла.
  const visibleFilters = me.is_admin ? FILTERS : FILTERS.filter((f) => f.value !== 'managers')

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 300)
    return () => clearTimeout(timer)
  }, [query])

  const results = useSearch(debounced, types)
  const history = useSearchHistory()
  // Пять последних диалогов: список и так отсортирован по последнему сообщению.
  const recent = useConversations({ filter: 'all' })
  // ТЗ Б.15: десять строк, а не пять.
  const recentChats = (recent.data?.pages[0]?.items ?? []).slice(0, 10)
  const clearHistory = useClearHistory()

  const ready = debounced.length >= MIN_QUERY
  const groups = results.data
  const total = groups
    ? groups.clients.length +
      groups.deals.length +
      groups.chats.length +
      groups.files.length +
      (groups.managers?.length ?? 0)
    : 0

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-line px-4 py-3">
        <div className="mx-auto flex w-full max-w-2xl items-center gap-2">
          <BackButton fallback="/chats" />
          <Input
            className="flex-1"
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Клиенты, оплаты, чаты, файлы, менеджеры"
            leading={<Search className="size-4" aria-hidden />}
            trailing={
              query ? (
                <button
                  aria-label="Очистить"
                  onClick={() => setQuery('')}
                  className="text-ink-faint transition-colors hover:text-ink"
                >
                  <X className="size-4" aria-hidden />
                </button>
              ) : undefined
            }
          />
        </div>

        <div className="mx-auto mt-2 flex w-full max-w-2xl flex-wrap gap-1.5">
          {visibleFilters.map((filter) => {
            const active = types.includes(filter.value)
            return (
              <button
                key={filter.value}
                onClick={() =>
                  setTypes((prev) =>
                    active ? prev.filter((t) => t !== filter.value) : [...prev, filter.value],
                  )
                }
                className={cn(
                  'min-h-9 rounded px-3 py-2 text-xs transition-colors',
                  active ? 'bg-accent-soft text-accent-text' : 'bg-surface-raised text-ink-muted',
                )}
              >
                {filter.label}
              </button>
            )
          })}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-5">
          {!ready ? (
            <>
              <p className="text-xs text-ink-faint">
                Нужно минимум два символа. Ищет по ID клиента и номеру сделки — например 84271
                или DEAL-1042.
              </p>
              {/* Доска, п.1: под строкой поиска — недавние диалоги И история.
                  Обычно человек ищет того, с кем только что переписывался. */}
              <section className="flex flex-col gap-2">
                <SectionTitle>Недавние диалоги</SectionTitle>
                {recent.isLoading ? (
                  <ListSkeleton rows={3} />
                ) : recentChats.length === 0 ? (
                  <EmptyState title="Диалогов пока нет" />
                ) : (
                  <ul className="flex flex-col gap-1">
                    {recentChats.map((chat) => (
                      <li key={chat.id}>
                        <Link
                          to={`/chats/${chat.id}`}
                          className="flex items-center gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-surface-raised"
                        >
                          <Avatar name={chat.client.name} size="sm" />
                          <span className="min-w-0 flex-1 truncate text-sm text-ink">
                            {chat.client.name}
                          </span>
                          <span className="shrink-0 text-micro text-ink-faint">
                            {chat.account.title}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="flex flex-col gap-2">
                <SectionTitle
                  action={
                    (history.data ?? []).length > 0 ? (
                      <button
                        onClick={() => clearHistory.mutate()}
                        className="-my-1 min-h-9 px-1 py-1 text-xs text-accent-text transition-colors hover:text-accent"
                      >
                        Очистить
                      </button>
                    ) : undefined
                  }
                >
                  Недавние запросы
                </SectionTitle>
                {history.isLoading ? (
                  <ListSkeleton rows={3} />
                ) : (history.data ?? []).length === 0 ? (
                  <EmptyState title="История пуста" hint="Здесь появятся последние 10 запросов." />
                ) : (
                  <ul className="flex flex-col gap-1">
                    {(history.data ?? []).map((item) => (
                      <li key={item.id}>
                        <button
                          onClick={() => setQuery(item.query)}
                          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
                        >
                          <Search className="size-4 shrink-0 text-ink-faint" aria-hidden />
                          {item.query}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          ) : results.isLoading ? (
            <ListSkeleton rows={6} />
          ) : results.error ? (
            <ErrorState error={results.error} onRetry={() => void results.refetch()} />
          ) : total === 0 ? (
            <EmptyState
              title={`По запросу «${debounced}» ничего не найдено`}
              hint="Проверьте написание или снимите фильтры."
            />
          ) : (
            <>
              {groups!.clients.length > 0 && (
                <Group title="Клиенты" count={groups!.clients.length}>
                  {groups!.clients.map((row) => (
                    <Row
                      key={row.id}
                      to={`/clients/${row.id}`}
                      icon={<User className="size-4" aria-hidden />}
                      title={row.name}
                      hint={`id ${row.id}${row.phone ? ` · ${row.phone}` : ''}`}
                      right={row.paid_amount > 0 ? money(row.paid_amount) : undefined}
                    />
                  ))}
                </Group>
              )}

              {groups!.deals.length > 0 && (
                <Group title="Оплаты" count={groups!.deals.length}>
                  {groups!.deals.map((row) => (
                    <Row
                      key={row.id}
                      to={`/payments/${row.id}`}
                      icon={<CreditCard className="size-4" aria-hidden />}
                      title={row.title}
                      hint={`${row.number} · ${row.client_name}`}
                      right={money(row.total_amount)}
                      badge={
                        <Badge tone={DEAL_STATUS_TONE[row.status]}>
                          {DEAL_STATUS_LABEL[row.status]}
                        </Badge>
                      }
                    />
                  ))}
                </Group>
              )}

              {groups!.chats.length > 0 && (
                <Group title="Чаты" count={groups!.chats.length}>
                  {groups!.chats.map((row) => (
                    <Row
                      key={row.message_id}
                      to={`/chats/${row.conversation_id}`}
                      icon={<MessageSquare className="size-4" aria-hidden />}
                      title={row.client_name}
                      hint={row.snippet}
                      right={dateShort(row.created_at)}
                    />
                  ))}
                </Group>
              )}

              {(groups!.managers ?? []).length > 0 && (
                <Group title="Менеджеры" count={groups!.managers.length}>
                  {groups!.managers.map((row) => (
                    <Row
                      key={row.id}
                      to={`/profile/staff?user=${row.id}`}
                      icon={<User className="size-4" aria-hidden />}
                      title={row.full_name}
                      hint={row.email}
                      right={row.online ? 'в сети' : 'не в сети'}
                    />
                  ))}
                </Group>
              )}

              {groups!.files.length > 0 && (
                <Group title="Файлы" count={groups!.files.length}>
                  {groups!.files.map((row) => (
                    <Row
                      key={row.id}
                      href={row.url}
                      icon={<FileText className="size-4" aria-hidden />}
                      title={row.file_name}
                      hint={row.client_name}
                      right={dateShort(row.created_at)}
                    />
                  ))}
                </Group>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function Group({
  title,
  count,
  children,
}: {
  title: string
  count: number
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>
        {title} · {count}
      </SectionTitle>
      <ul className="flex flex-col gap-1">{children}</ul>
    </section>
  )
}

function Row({
  to,
  href,
  icon,
  title,
  hint,
  right,
  badge,
}: {
  to?: string
  href?: string
  icon: React.ReactNode
  title: string
  hint?: string
  right?: string
  badge?: React.ReactNode
}) {
  const body = (
    <>
      <span className="flex size-8 shrink-0 items-center justify-center rounded bg-surface-raised text-ink-faint">
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate text-sm text-ink">{title}</span>
        {hint && <span className="truncate text-micro text-ink-faint">{hint}</span>}
      </span>
      {badge}
      {right && <span className="tnum shrink-0 text-xs text-ink-muted">{right}</span>}
    </>
  )

  const className =
    'flex items-center gap-3 rounded-md bg-surface px-3 py-2.5 transition-colors hover:bg-surface-raised'

  return (
    <li>
      {href ? (
        <a href={href} target="_blank" rel="noreferrer" className={className}>
          {body}
        </a>
      ) : (
        <Link to={to ?? '#'} className={className}>
          {body}
        </Link>
      )}
    </li>
  )
}
