import { Bell, Search } from 'lucide-react'
import { Link } from 'react-router-dom'

import { cn } from '@/shared/lib/cn'
import { useUnreadCount } from '@/features/profile/queries'

/**
 * Поиск и уведомления в шапке раздела.
 *
 * На доске поиск открывается «из любого раздела: чаты, клиенты, оплаты»,
 * поэтому блок один и ставится во все три шапки, а не пишется заново в каждой.
 */
export function SectionActions({ className }: { className?: string }) {
  const unread = useUnreadCount()
  const count = unread.data?.count ?? 0

  return (
    <div className={cn('flex shrink-0 items-center gap-1', className)}>
      <Link
        to="/search"
        aria-label="Поиск"
        title="Поиск по клиентам, оплатам, чатам и файлам"
        className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
      >
        <Search className="size-5" aria-hidden />
      </Link>

      <Link
        to="/profile"
        aria-label={count > 0 ? `Уведомления, непрочитанных: ${count}` : 'Уведомления'}
        title="Уведомления"
        className="relative flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
      >
        <Bell className="size-5" aria-hidden />
        {count > 0 && (
          <span className="tnum absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold leading-none text-white">
            {count > 9 ? '9+' : count}
          </span>
        )}
      </Link>
    </div>
  )
}
