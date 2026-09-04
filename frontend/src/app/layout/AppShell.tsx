/**
 * Оболочка приложения. Один код, две раскладки — граница 1024px.
 *
 * Мобильная: нижняя навигация из пяти вкладок.
 * Десктоп: левый вертикальный рельс иконок, дальше содержимое раздела.
 */

import {
  BarChart3,
  CreditCard,
  MessageSquare,
  Settings,
  User,
  Users,
  WifiOff,
} from 'lucide-react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import type { Counters } from '@/entities/types'
import { useAuth } from '@/shared/hooks/useAuth'
import { useRealtimeSync } from '@/shared/hooks/useRealtime'
import { cn } from '@/shared/lib/cn'
import { Avatar } from '@/shared/ui'
import { useCounters } from '@/features/chats/queries'

interface NavItem {
  to: string
  label: string
  icon: typeof MessageSquare
  badge?: (counters: Counters | undefined) => number | undefined
}

const NAV: NavItem[] = [
  {
    to: '/chats',
    label: 'Чаты',
    icon: MessageSquare,
    badge: (c) => c?.awaiting,
  },
  { to: '/clients', label: 'Клиенты', icon: Users },
  {
    to: '/payments',
    label: 'Оплаты',
    icon: CreditCard,
    badge: (c) => c?.awaiting_payment,
  },
  { to: '/stats', label: 'Статистика', icon: BarChart3 },
  { to: '/profile', label: 'Профиль', icon: User },
]

export function AppShell() {
  const status = useRealtimeSync()
  const { me } = useAuth()
  const { data: counters } = useCounters()
  const location = useLocation()

  // На мобильном внутри открытого чата нижняя навигация мешает — прячем её.
  const hideMobileNav = /^\/chats\/\d+/.test(location.pathname)

  return (
    <div className="flex h-full flex-col desk:flex-row">
      {status === 'offline' && (
        <div
          role="status"
          className="flex shrink-0 items-center justify-center gap-2 bg-warning-soft px-3 py-1.5 text-xs text-warning"
        >
          <WifiOff className="size-3.5" aria-hidden />
          Нет связи с сервером. Восстанавливаем соединение…
        </div>
      )}

      {/* Десктопный рельс */}
      <nav
        aria-label="Разделы"
        className="hidden w-14 shrink-0 flex-col items-center gap-1 border-r border-line bg-surface py-3 desk:flex"
      >
        <span className="mb-3 text-xl" aria-hidden>
          🌙
        </span>
        {NAV.filter((item) => item.to !== '/profile').map((item) => (
          <RailLink key={item.to} item={item} counters={counters} />
        ))}
        <div className="mt-auto flex flex-col items-center gap-1">
          {me?.role === 'admin' && (
            <RailLink
              item={{ to: '/settings', label: 'Настройки', icon: Settings }}
              counters={counters}
            />
          )}
          <NavLink
            to="/profile"
            aria-label="Профиль"
            title="Профиль"
            className="mt-1 rounded-full ring-offset-2 ring-offset-surface focus-visible:ring-2"
          >
            {me && <Avatar name={me.full_name} color={me.avatar_color} size="sm" />}
          </NavLink>
        </div>
      </nav>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Outlet />
      </main>

      {/* Мобильная нижняя навигация */}
      {!hideMobileNav && (
        <nav
          aria-label="Разделы"
          className="h-nav flex shrink-0 items-stretch border-t border-line bg-surface pb-safe desk:hidden"
        >
          {NAV.map((item) => (
            <TabLink key={item.to} item={item} counters={counters} />
          ))}
        </nav>
      )}
    </div>
  )
}

function RailLink({ item, counters }: { item: NavItem; counters: Counters | undefined }) {
  const badge = item.badge?.(counters)
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      aria-label={item.label}
      title={item.label}
      className={({ isActive }) =>
        cn(
          'relative flex size-10 items-center justify-center rounded transition-colors',
          isActive
            ? 'bg-accent-soft text-accent-text'
            : 'text-ink-faint hover:bg-surface-raised hover:text-ink',
        )
      }
    >
      <Icon className="size-5" aria-hidden />
      {badge ? <Counter value={badge} className="-right-0.5 -top-0.5" /> : null}
    </NavLink>
  )
}

function TabLink({ item, counters }: { item: NavItem; counters: Counters | undefined }) {
  const badge = item.badge?.(counters)
  const Icon = item.icon
  return (
    <NavLink
      to={item.to}
      className={({ isActive }) =>
        cn(
          'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5',
          'px-0.5 text-[10px] leading-tight transition-colors',
          isActive ? 'text-accent-text' : 'text-ink-faint',
        )
      }
    >
      <span className="relative">
        <Icon className="size-5" aria-hidden />
        {badge ? <Counter value={badge} className="-right-2 -top-1" /> : null}
      </span>
      <span className="w-full truncate text-center">{item.label}</span>
    </NavLink>
  )
}

function Counter({ value, className }: { value: number; className?: string }) {
  return (
    <span
      className={cn(
        'tnum absolute flex h-4 min-w-4 items-center justify-center rounded-full',
        'bg-accent px-1 text-[10px] font-semibold leading-none text-white',
        className,
      )}
    >
      {value > 99 ? '99+' : value}
    </span>
  )
}
