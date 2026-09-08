/**
 * Оболочка экранов раздела: на телефоне — полноэкранная страница со стрелкой
 * назад, на десктопе — центральная колонка шириной max-w-3xl.
 */

import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { BackButton } from '@/shared/ui'

export function ProfileScreen({
  title,
  subtitle,
  backTo,
  action,
  children,
}: {
  title: string
  subtitle?: string
  backTo?: string
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-line px-4 py-3 desk:px-6">
        <div className="mx-auto flex w-full max-w-3xl items-center gap-3">
          {backTo && <BackButton fallback={backTo} className="-ml-2" />}
          <div className="flex min-w-0 flex-1 flex-col">
            <h1 className="truncate text-lg font-semibold text-ink">{title}</h1>
            {subtitle && <p className="truncate text-xs text-ink-muted">{subtitle}</p>}
          </div>
          {action}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-4 desk:px-6 desk:py-6">
          {children}
        </div>
      </div>
    </div>
  )
}

/** Строка-переход в подраздел. */
export function NavRow({
  to,
  icon,
  title,
  hint,
}: {
  to: string
  icon: ReactNode
  title: string
  hint?: string
}) {
  return (
    <Link
      to={to}
      className="flex items-center gap-3 rounded-lg bg-surface px-4 py-3 transition-colors hover:bg-surface-raised"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded bg-surface-raised text-ink-muted">
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-sm font-medium text-ink">{title}</span>
        {hint && <span className="truncate text-xs text-ink-muted">{hint}</span>}
      </span>
      <ChevronRight className="size-4 shrink-0 text-ink-faint" aria-hidden />
    </Link>
  )
}
