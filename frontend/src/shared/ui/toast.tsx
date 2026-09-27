import { CheckCircle2, X } from 'lucide-react'
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { create } from 'zustand'

/**
 * Короткие подтверждения действий: «Скопировано», «Переслано в чат …».
 * Не для ошибок — ошибка показывается там, где случилась, и не исчезает сама.
 */

interface Toast {
  id: number
  text: string
  link?: { to: string; label: string }
}

interface ToastState {
  items: Toast[]
  push: (toast: Omit<Toast, 'id'>) => void
  dismiss: (id: number) => void
}

let nextId = 1

export const useToasts = create<ToastState>((set) => ({
  items: [],
  push: (toast) =>
    set((state) => ({ items: [...state.items.slice(-2), { ...toast, id: nextId++ }] })),
  dismiss: (id) => set((state) => ({ items: state.items.filter((item) => item.id !== id) })),
}))

export function toast(text: string, link?: Toast['link']) {
  useToasts.getState().push({ text, link })
}

function ToastItem({ item }: { item: Toast }) {
  const dismiss = useToasts((state) => state.dismiss)
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(item.id), 4000)
    return () => window.clearTimeout(timer)
  }, [dismiss, item.id])
  return (
    <div
      role="status"
      className="pointer-events-auto flex items-center gap-2.5 rounded-md border border-line-strong bg-surface-raised px-3 py-2.5 shadow-sheet"
    >
      <CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />
      <span className="text-sm text-ink">{item.text}</span>
      {item.link && (
        <Link
          to={item.link.to}
          onClick={() => dismiss(item.id)}
          className="text-sm text-accent-text underline-offset-4 hover:underline"
        >
          {item.link.label}
        </Link>
      )}
      <button
        type="button"
        aria-label="Закрыть"
        onClick={() => dismiss(item.id)}
        className="text-ink-faint transition-colors hover:text-ink"
      >
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  )
}

export function Toaster() {
  const items = useToasts((state) => state.items)
  if (items.length === 0) return null
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 desk:bottom-6">
      {items.map((item) => (
        <ToastItem key={item.id} item={item} />
      ))}
    </div>
  )
}
