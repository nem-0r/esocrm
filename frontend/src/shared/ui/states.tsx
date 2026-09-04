/**
 * Четыре состояния каждого экрана: загрузка, пусто, ошибка, данные.
 * Экран без всех четырёх считается недоделанным.
 *
 * Ошибка всегда видима и всегда с кнопкой «Повторить». Молча подставлять
 * демо-данные вместо ответа сервера запрещено — в прошлой версии продукта
 * именно это давало самые дорогие баги.
 */

import { AlertTriangle, Inbox, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'

import { ApiError } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { Button, Skeleton, Spinner } from '@/shared/ui/primitives'

export function LoadingState({ className }: { className?: string }) {
  return (
    <div className={cn('flex flex-1 items-center justify-center py-16', className)}>
      <Spinner />
    </div>
  )
}

export function EmptyState({
  title,
  hint,
  action,
  icon,
  className,
}: {
  title: string
  hint?: string
  action?: ReactNode
  icon?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-1 flex-col items-center justify-center gap-3 px-6 py-16 text-center',
        className,
      )}
    >
      <span className="text-ink-faint">{icon ?? <Inbox className="size-7" aria-hidden />}</span>
      <p className="text-base font-medium text-ink">{title}</p>
      {hint && <p className="max-w-sm text-sm text-ink-muted">{hint}</p>}
      {action}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown
  onRetry?: () => void
  className?: string
}) {
  const message =
    error instanceof ApiError
      ? error.message
      : error instanceof Error
        ? error.message
        : 'Неизвестная ошибка'

  return (
    <div
      className={cn(
        'flex flex-1 flex-col items-center justify-center gap-3 px-6 py-16 text-center',
        className,
      )}
      role="alert"
    >
      <AlertTriangle className="size-7 text-danger" aria-hidden />
      <p className="text-base font-medium text-ink">Не удалось загрузить</p>
      <p className="max-w-sm text-sm text-ink-muted">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          <RefreshCw className="size-4" aria-hidden />
          Повторить
        </Button>
      )}
    </div>
  )
}

/** Полоса-предупреждение внутри экрана — когда данные есть, но что-то не так. */
export function InlineError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-center gap-2 rounded border border-danger/40 bg-danger-soft px-3 py-2 text-sm text-danger"
    >
      <AlertTriangle className="size-4 shrink-0" aria-hidden />
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button onClick={onRetry} className="shrink-0 font-medium underline underline-offset-2">
          Повторить
        </button>
      )}
    </div>
  )
}

export function ListSkeleton({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('flex flex-col', className)} aria-busy="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3 border-b border-line px-4 py-3">
          <Skeleton className="size-9 rounded-full" />
          <div className="flex flex-1 flex-col gap-1.5">
            <Skeleton className="h-3.5 w-32" />
            <Skeleton className="h-3 w-48" />
          </div>
          <Skeleton className="h-3 w-10" />
        </div>
      ))}
    </div>
  )
}

export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn('flex flex-col gap-3 rounded-lg bg-surface p-4', className)} aria-busy="true">
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-3/4" />
    </div>
  )
}

/**
 * Единая обёртка над состояниями запроса.
 * Используется так, чтобы ни один экран не забыл про пустое состояние и ошибку.
 */
export function QueryState<T>({
  isLoading,
  error,
  data,
  onRetry,
  empty,
  skeleton,
  children,
}: {
  isLoading: boolean
  error: unknown
  data: T | undefined
  onRetry?: () => void
  empty?: ReactNode
  skeleton?: ReactNode
  children: (data: T) => ReactNode
}) {
  if (isLoading) return <>{skeleton ?? <LoadingState />}</>
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  if (data === undefined || data === null) return <>{empty ?? <EmptyState title="Пусто" />}</>
  if (Array.isArray(data) && data.length === 0) {
    return <>{empty ?? <EmptyState title="Пока ничего нет" />}</>
  }
  return <>{children(data)}</>
}
