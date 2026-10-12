import { Bell, ChevronDown, ChevronRight, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type { AppNotification } from '@/entities/types'
import { cn } from '@/shared/lib/cn'
import { dateTimeFull } from '@/shared/lib/format'
import { useAuth } from '@/shared/hooks/useAuth'
import { Dot, EmptyState, ErrorState, InlineError, ListSkeleton, Sheet } from '@/shared/ui'
import { errorMessage } from '@/features/profile/lib'
import {
  useMarkNotificationsRead,
  useNotificationsFeed,
  useUnreadCount,
} from '@/features/profile/queries'
import { notificationTarget } from '@/features/profile/rules'

// Свёрнутый список показывает три верхних уведомления: остальные прячутся
// под кнопку. ТЗ п. 1.3 — список должен раскрываться и сворачиваться.
const COLLAPSED_ROWS = 3

/**
 * Окно «Уведомления». Открывается колокольчиком в шапке любого раздела и
 * строкой в профиле — состояние открытия держит тот, кто его показывает, а
 * список окно запрашивает само и только пока открыто.
 *
 * Строка уведомления — кнопка: помечает его прочитанным, закрывает окно и
 * ведёт к объекту (оплата → сделка, аккаунт → «Аккаунты» или «Профиль»).
 * Если вести некуда, строка остаётся обычным текстом.
 */
export function NotificationsSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { isAdmin } = useAuth()
  const navigate = useNavigate()
  const feed = useNotificationsFeed(open)
  const unread = useUnreadCount()
  const markRead = useMarkNotificationsRead()
  const [expanded, setExpanded] = useState(false)

  const rows = feed.data?.pages.flatMap((page) => page.items) ?? []
  const visible = expanded ? rows : rows.slice(0, COLLAPSED_ROWS)
  const hidden = rows.length - visible.length
  const unreadCount = unread.data?.count ?? 0

  function changeOpen(next: boolean) {
    if (!next) {
      setExpanded(false)
      markRead.reset()
    }
    onOpenChange(next)
  }

  function openRow(row: AppNotification, target: string) {
    if (row.read_at === null) markRead.mutate([row.id])
    changeOpen(false)
    navigate(target)
  }

  return (
    <Sheet
      open={open}
      onOpenChange={changeOpen}
      title="Уведомления"
      description="За последний месяц"
      className="desk:w-[460px]"
    >
      <div className="flex flex-col gap-2">
        {unreadCount > 0 && (
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => markRead.mutate(undefined)}
              disabled={markRead.isPending}
              className="-my-1 min-h-9 px-1 py-1 text-xs text-accent-text transition-colors hover:text-accent disabled:opacity-45"
            >
              Отметить все прочитанными
            </button>
          </div>
        )}

        {markRead.isError && <InlineError message={errorMessage(markRead.error)} />}

        {feed.isLoading ? (
          <ListSkeleton rows={3} />
        ) : feed.error ? (
          <ErrorState error={feed.error} onRetry={() => void feed.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Уведомлений нет"
            hint="Здесь появятся оплаты и изменения по аккаунтам."
            icon={<Bell className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-1">
            {visible.map((row) => {
              const target = notificationTarget(row, isAdmin)
              const isUnread = row.read_at === null
              const body = (
                <>
                  {isUnread && <Dot tone="accent" />}
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="text-sm text-ink">{row.title}</span>
                    <span className="break-words text-xs text-ink-muted">{row.text}</span>
                    <span className="text-micro text-ink-faint">{dateTimeFull(row.created_at)}</span>
                  </div>
                  {target && (
                    <ChevronRight className="size-4 shrink-0 self-center text-ink-faint" aria-hidden />
                  )}
                </>
              )
              const look = cn(
                'flex w-full items-start gap-2 rounded-md px-3 py-2.5 text-left',
                isUnread ? 'bg-accent-soft' : 'bg-surface-raised',
              )
              return (
                <li key={row.id}>
                  {target ? (
                    <button
                      type="button"
                      onClick={() => openRow(row, target)}
                      className={cn(look, 'transition-colors hover:bg-line')}
                    >
                      {body}
                    </button>
                  ) : (
                    <div className={look}>{body}</div>
                  )}
                </li>
              )
            })}
          </ul>
        )}

        {rows.length > COLLAPSED_ROWS && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="flex min-h-9 items-center justify-center gap-1.5 rounded-md text-xs text-accent-text transition-colors hover:bg-surface-raised"
          >
            {expanded ? (
              <>
                <ChevronUp className="size-4" aria-hidden />
                Свернуть
              </>
            ) : (
              <>
                <ChevronDown className="size-4" aria-hidden />
                Развернуть все · ещё {hidden}
                {feed.hasNextPage ? '+' : ''}
              </>
            )}
          </button>
        )}

        {expanded && feed.hasNextPage && (
          <button
            type="button"
            onClick={() => void feed.fetchNextPage()}
            disabled={feed.isFetchingNextPage}
            className="flex min-h-9 items-center justify-center rounded-md text-xs text-accent-text transition-colors hover:bg-surface-raised disabled:opacity-45"
          >
            {feed.isFetchingNextPage ? 'Загружаем…' : 'Показать ещё'}
          </button>
        )}
      </div>
    </Sheet>
  )
}
