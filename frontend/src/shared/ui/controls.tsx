import * as SelectPrimitive from '@radix-ui/react-select'
import * as SwitchPrimitive from '@radix-ui/react-switch'
import { Check, ChevronDown } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/shared/lib/cn'

/** Полоса фильтров: «Все · Ждут ответа · Ожидают оплаты». Прокручивается на телефоне. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  className,
}: {
  value: T
  onChange: (value: T) => void
  options: { value: T; label: string; count?: number; tone?: 'danger' | 'accent' }[]
  className?: string
}) {
  return (
    // На телефоне ряд прокручивается пальцем — привычный жест. На десктопе
    // прокрутка без полосы незаметна мышью, поэтому вкладки переносятся строкой.
    <div
      className={cn(
        'no-scrollbar flex gap-1.5 overflow-x-auto desk:flex-wrap desk:overflow-x-visible',
        className,
      )}
      role="tablist"
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex shrink-0 items-center gap-1.5 rounded px-3 py-1.5 text-sm transition-colors',
              active
                ? 'bg-accent-soft text-accent-text'
                : 'text-ink-muted hover:bg-surface-raised hover:text-ink',
            )}
          >
            {option.label}
            {option.count !== undefined && option.count > 0 && (
              <span
                className={cn(
                  'tnum text-micro font-semibold',
                  option.tone === 'danger'
                    ? 'text-danger'
                    : active
                      ? 'text-accent-text'
                      : 'text-ink-faint',
                )}
              >
                {option.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function Switch({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  label?: string
  hint?: string
  disabled?: boolean
}) {
  const control = (
    <SwitchPrimitive.Root
      checked={checked}
      onCheckedChange={onChange}
      disabled={disabled}
      aria-label={label}
      className={cn(
        'relative h-6 w-11 shrink-0 rounded-full transition-colors',
        // Сама дорожка 24px — этого мало для пальца. Псевдоэлемент расширяет
        // область нажатия до 44px, не меняя внешний вид.
        'before:absolute before:-inset-y-2.5 before:-inset-x-1 before:content-[""]',
        'data-[state=checked]:bg-accent data-[state=unchecked]:bg-line-strong',
        'disabled:opacity-45',
      )}
    >
      <SwitchPrimitive.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white transition-transform data-[state=checked]:translate-x-[22px]" />
    </SwitchPrimitive.Root>
  )

  if (!label) return control

  return (
    <label className="flex cursor-pointer items-center justify-between gap-4">
      <span className="flex flex-col gap-0.5">
        <span className="text-sm text-ink">{label}</span>
        {hint && <span className="text-xs text-ink-muted">{hint}</span>}
      </span>
      {control}
    </label>
  )
}

export function Select<T extends string>({
  value,
  onChange,
  options,
  placeholder = 'Выберите',
  className,
  disabled,
}: {
  value: T | null
  onChange: (value: T) => void
  options: { value: T; label: string; hint?: string }[]
  placeholder?: string
  className?: string
  disabled?: boolean
}) {
  return (
    <SelectPrimitive.Root value={value ?? undefined} onValueChange={onChange} disabled={disabled}>
      <SelectPrimitive.Trigger
        className={cn(
          'flex h-10 w-full items-center justify-between gap-2 rounded border border-line-strong',
          'bg-surface-raised px-3 text-sm text-ink transition-colors',
          'focus:border-accent focus:outline-none disabled:opacity-45',
          'data-[placeholder]:text-ink-faint',
          className,
        )}
      >
        <SelectPrimitive.Value placeholder={placeholder} />
        <SelectPrimitive.Icon>
          <ChevronDown className="size-4 text-ink-faint" aria-hidden />
        </SelectPrimitive.Icon>
      </SelectPrimitive.Trigger>
      <SelectPrimitive.Portal>
        <SelectPrimitive.Content
          position="popper"
          sideOffset={4}
          className="z-[60] max-h-72 w-[--radix-select-trigger-width] overflow-hidden rounded-md border border-line-strong bg-surface shadow-popover"
        >
          <SelectPrimitive.Viewport className="p-1">
            {options.map((option) => (
              <SelectPrimitive.Item
                key={option.value}
                value={option.value}
                className={cn(
                  'flex cursor-pointer select-none items-start justify-between gap-2 rounded px-2.5 py-2',
                  'text-sm text-ink outline-none data-[highlighted]:bg-surface-raised',
                )}
              >
                <span className="flex flex-col gap-0.5">
                  <SelectPrimitive.ItemText>{option.label}</SelectPrimitive.ItemText>
                  {option.hint && <span className="text-xs text-ink-faint">{option.hint}</span>}
                </span>
                <SelectPrimitive.ItemIndicator>
                  <Check className="mt-0.5 size-4 text-accent-text" aria-hidden />
                </SelectPrimitive.ItemIndicator>
              </SelectPrimitive.Item>
            ))}
          </SelectPrimitive.Viewport>
        </SelectPrimitive.Content>
      </SelectPrimitive.Portal>
    </SelectPrimitive.Root>
  )
}

/** Заголовок секции внутри карточки. */
export function SectionTitle({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-label uppercase tracking-wide text-ink-faint">{children}</h3>
      {action}
    </div>
  )
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <section className={cn('flex flex-col gap-3 rounded-lg bg-surface p-4', className)}>
      {children}
    </section>
  )
}

export function StatTile({
  value,
  label,
  tone = 'neutral',
  delta,
}: {
  value: ReactNode
  label: string
  tone?: 'neutral' | 'success' | 'accent' | 'warning'
  delta?: ReactNode
}) {
  const tones = {
    neutral: 'text-ink',
    success: 'text-success',
    accent: 'text-accent-text',
    warning: 'text-warning',
  }
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg bg-surface p-3.5">
      {/* Цифру не обрезаем: «269 10…» вместо «269 100 ₽» — это главная величина
          экрана, которую нельзя прочитать. В узкой плитке уменьшаем шрифт и
          позволяем перенос по неразрывным пробелам внутри суммы. */}
      <span
        className={cn(
          'tnum text-xl font-semibold leading-tight [overflow-wrap:anywhere]',
          'max-[400px]:text-lg',
          tones[tone],
        )}
      >
        {value}
      </span>
      {/* Подпись переносится, а не обрезается: «оплачено за период» должно
          читаться целиком даже в узкой плитке на телефоне. */}
      <span className="text-balance text-xs leading-snug text-ink-muted">{label}</span>
      {delta}
    </div>
  )
}
