/**
 * Модальные окна.
 *
 * На мобильном они выезжают снизу и закрываются крестиком или свайпом вниз —
 * так на макетах. На десктопе то же окно становится центральным диалогом.
 */

import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'

import { cn } from '@/shared/lib/cn'
import { Button } from '@/shared/ui/primitives'

export function Sheet({
  open,
  onOpenChange,
  title,
  description,
  footer,
  children,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  footer?: ReactNode
  children: ReactNode
  className?: string
}) {
  const contentRef = useRef<HTMLDivElement>(null)
  const [dragY, setDragY] = useState(0)
  const startY = useRef<number | null>(null)

  useEffect(() => {
    if (!open) setDragY(0)
  }, [open])

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 animate-fade-in bg-black/70" />
        <Dialog.Content
          ref={contentRef}
          style={dragY ? { transform: `translateY(${dragY}px)` } : undefined}
          className={cn(
            'fixed inset-x-0 bottom-0 z-50 flex max-h-[92dvh] flex-col',
            'animate-sheet-up rounded-t-xl border-t border-line bg-surface shadow-sheet',
            'desk:inset-x-auto desk:bottom-auto desk:left-1/2 desk:top-1/2 desk:w-[560px]',
            'desk:-translate-x-1/2 desk:-translate-y-1/2 desk:rounded-xl desk:border',
            className,
          )}
        >
          {/* Полоска для свайпа — только на мобильном */}
          <div
            className="flex shrink-0 cursor-grab justify-center pb-1 pt-2 desk:hidden"
            onTouchStart={(e) => (startY.current = e.touches[0].clientY)}
            onTouchMove={(e) => {
              if (startY.current === null) return
              const delta = e.touches[0].clientY - startY.current
              if (delta > 0) setDragY(delta)
            }}
            onTouchEnd={() => {
              if (dragY > 110) onOpenChange(false)
              else setDragY(0)
              startY.current = null
            }}
          >
            <span className="h-1 w-10 rounded-full bg-line-strong" />
          </div>

          <div className="flex shrink-0 items-start justify-between gap-3 px-4 pb-3 pt-2 desk:pt-4">
            <div className="flex flex-col gap-0.5">
              <Dialog.Title className="text-lg font-semibold text-ink">{title}</Dialog.Title>
              {description && (
                <Dialog.Description className="text-sm text-ink-muted">
                  {description}
                </Dialog.Description>
              )}
            </div>
            <Dialog.Close asChild>
              <button
                aria-label="Закрыть"
                className="-mr-1 -mt-1 flex size-9 shrink-0 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
              >
                <X className="size-5" aria-hidden />
              </button>
            </Dialog.Close>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">{children}</div>

          {footer && (
            <div className="shrink-0 border-t border-line bg-surface px-4 py-3 pb-safe desk:pb-3">
              {footer}
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  message,
  confirmLabel = 'Подтвердить',
  cancelLabel = 'Отмена',
  danger,
  loading,
  onConfirm,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  loading?: boolean
  onConfirm: () => void
  children?: ReactNode
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={message}
      className="desk:w-[440px]"
      footer={
        <div className="flex gap-2">
          <Button variant="secondary" fullWidth onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? 'danger' : 'primary'}
            fullWidth
            loading={loading}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </div>
      }
    >
      {children}
    </Sheet>
  )
}
