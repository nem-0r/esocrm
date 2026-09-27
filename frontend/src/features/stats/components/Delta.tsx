import { TrendingDown, TrendingUp } from 'lucide-react'

import { cn } from '@/shared/lib/cn'

/** Прирост к прошлому периоду той же длины. Пусто — сравнивать не с чем. */
export function Delta({ percent, count }: { percent?: number | null; count?: number | null }) {
  const value = percent ?? count
  if (value === null || value === undefined) return null
  const positive = value >= 0
  const Icon = positive ? TrendingUp : TrendingDown
  return (
    <span
      className={cn(
        'tnum flex items-center gap-1 text-micro',
        positive ? 'text-success' : 'text-danger',
      )}
    >
      <Icon className="size-3" aria-hidden />
      {positive ? '+' : ''}
      {percent !== undefined && percent !== null ? `${value}%` : value}
    </span>
  )
}

/** Процент без лишних нулей: «65 %», «12,5 %». Пусто — «—». */
export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%`
}

/** Тонкая полоса доли — одна величина, один цвет, без легенды. */
export function ShareBar({ value }: { value: number | null | undefined }) {
  const width = Math.max(0, Math.min(100, value ?? 0))
  return (
    <span className="block h-1.5 w-full overflow-hidden rounded-full bg-line" aria-hidden>
      <span className="block h-full rounded-full bg-accent" style={{ width: `${width}%` }} />
    </span>
  )
}
