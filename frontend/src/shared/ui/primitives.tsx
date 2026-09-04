import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type TextareaHTMLAttributes } from 'react'
import { Loader2 } from 'lucide-react'

import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'

/* ------------------------------------------------------------------ кнопки */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
type ButtonSize = 'sm' | 'md' | 'lg'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover active:bg-accent',
  secondary: 'bg-surface-raised text-ink hover:bg-line border border-line-strong',
  ghost: 'text-ink-muted hover:text-ink hover:bg-surface-raised',
  danger: 'bg-danger-soft text-danger hover:bg-danger hover:text-white',
}

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-sm gap-1.5 rounded',
  md: 'h-10 px-4 text-sm gap-2 rounded',
  lg: 'h-12 px-5 text-base gap-2 rounded-md',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  fullWidth?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, fullWidth, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center font-medium transition-colors',
        'disabled:opacity-45 disabled:cursor-not-allowed',
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        fullWidth && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  )
})

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  active?: boolean
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, active, className, children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      aria-label={label}
      title={label}
      className={cn(
        'inline-flex size-9 shrink-0 items-center justify-center rounded transition-colors',
        active ? 'bg-accent-soft text-accent-text' : 'text-ink-muted hover:bg-surface-raised hover:text-ink',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
})

/* -------------------------------------------------------------------- поля */

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
  leading?: ReactNode
  trailing?: ReactNode
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { invalid, leading, trailing, className, ...rest },
  ref,
) {
  return (
    <div
      className={cn(
        'flex h-10 items-center gap-2 rounded border bg-surface-raised px-3 transition-colors',
        'focus-within:border-accent',
        invalid ? 'border-danger' : 'border-line-strong',
        className,
      )}
    >
      {leading && <span className="shrink-0 text-ink-faint">{leading}</span>}
      <input
        ref={ref}
        className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-ink-faint focus:outline-none"
        {...rest}
      />
      {trailing && <span className="shrink-0">{trailing}</span>}
    </div>
  )
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={cn(
          'w-full resize-none rounded border border-line-strong bg-surface-raised px-3 py-2',
          'text-sm text-ink placeholder:text-ink-faint transition-colors',
          'focus:border-accent focus:outline-none',
          className,
        )}
        {...rest}
      />
    )
  },
)

export function Field({
  label,
  hint,
  error,
  required,
  group,
  children,
}: {
  label: string
  hint?: string
  error?: string
  required?: boolean
  /** Внутри не одно поле, а набор кнопок (дни недели, аккаунты, способы оплаты). */
  group?: boolean
  children: ReactNode
}) {
  const caption = (
    <span className="text-label uppercase tracking-wide text-ink-faint">
      {label}
      {required && <span className="ml-1 text-danger">*</span>}
    </span>
  )
  const footer = error ? (
    <span className="text-xs text-danger">{error}</span>
  ) : hint ? (
    <span className="text-xs text-ink-faint">{hint}</span>
  ) : null

  // Для набора кнопок <label> не годится: браузер связывает подпись с первым
  // элементом внутри, и клик по «Дни недели» переключал понедельник. Плюс этот
  // первый элемент терял собственное имя для чтения с экрана.
  if (group) {
    return (
      <div role="group" aria-label={label} className="flex flex-col gap-1.5">
        {caption}
        {children}
        {footer}
      </div>
    )
  }

  return (
    <label className="flex flex-col gap-1.5">
      {caption}
      {children}
      {footer}
    </label>
  )
}

/* ------------------------------------------------------------------ бейджи */

type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'violet'

const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-raised text-ink-muted',
  accent: 'bg-accent-soft text-accent-text',
  success: 'bg-success-soft text-success',
  warning: 'bg-warning-soft text-warning',
  danger: 'bg-danger-soft text-danger',
  violet: 'bg-violet-soft text-violet-text',
}

export function Badge({
  tone = 'neutral',
  children,
  className,
}: {
  tone?: Tone
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-micro font-medium',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export function Dot({ tone = 'neutral' }: { tone?: Tone }) {
  const colors: Record<Tone, string> = {
    neutral: 'bg-ink-faint',
    accent: 'bg-accent',
    success: 'bg-success',
    warning: 'bg-warning',
    danger: 'bg-danger',
    violet: 'bg-violet',
  }
  return <span className={cn('inline-block size-1.5 shrink-0 rounded-full', colors[tone])} />
}

/* ----------------------------------------------------------------- аватары */

export function Avatar({
  name,
  color,
  size = 'md',
  online,
  className,
}: {
  name: string
  color?: string | null
  size?: 'sm' | 'md' | 'lg'
  online?: boolean
  className?: string
}) {
  const sizes = {
    sm: 'size-7 text-micro',
    md: 'size-9 text-xs',
    lg: 'size-12 text-base',
  }
  return (
    <span className={cn('relative inline-flex shrink-0', className)}>
      <span
        className={cn(
          'inline-flex items-center justify-center rounded-full font-semibold text-white/95',
          sizes[size],
        )}
        style={{ backgroundColor: color ?? '#7A6BE0' }}
        aria-hidden
      >
        {initials(name)}
      </span>
      {online !== undefined && (
        <span
          className={cn(
            'absolute -bottom-0 -right-0 size-2.5 rounded-full border-2 border-bg',
            online ? 'bg-success' : 'bg-ink-faint',
          )}
          aria-label={online ? 'в сети' : 'не в сети'}
        />
      )}
    </span>
  )
}

/* -------------------------------------------------------------- заглушки/ожидание */

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('size-5 animate-spin text-ink-faint', className)} aria-hidden />
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <span
      className={cn('relative block overflow-hidden rounded bg-surface-raised', className)}
      aria-hidden
    >
      <span className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/[0.04] to-transparent" />
    </span>
  )
}
