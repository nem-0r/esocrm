import { AlertCircle, CheckCircle2, X } from 'lucide-react'
import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { create } from 'zustand'

/**
 * Короткие уведомления о действиях: «Скопировано», «Переслано в чат …».
 * Ошибку формы показывают у самой формы; сюда — только ошибки действий, у
 * которых своего места нет (копирование, скачивание): `toastError`.
 */

interface Toast {
  id: number
  text: string
  link?: { to: string; label: string }
  kind: 'success' | 'error'
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
  useToasts.getState().push({ text, link, kind: 'success' })
}

export function toastError(text: string) {
  useToasts.getState().push({ text, kind: 'error' })
}

// Ошибку и уведомление со ссылкой надо успеть прочитать и нажать.
function lifetime(item: Toast): number {
  if (item.kind === 'error') return 8000
  return item.link ? 7000 : 4000
}

function ToastItem({ item }: { item: Toast }) {
  const dismiss = useToasts((state) => state.dismiss)
  useEffect(() => {
    const timer = window.setTimeout(() => dismiss(item.id), lifetime(item))
    return () => window.clearTimeout(timer)
  }, [dismiss, item])
  return (
    <div
      role={item.kind === 'error' ? 'alert' : 'status'}
      className="pointer-events-auto flex items-center gap-2.5 rounded-md border border-line-strong bg-surface-raised px-3 py-2.5 shadow-sheet"
    >
      {item.kind === 'error' ? (
        <AlertCircle className="size-4 shrink-0 text-danger" aria-hidden />
      ) : (
        <CheckCircle2 className="size-4 shrink-0 text-success" aria-hidden />
      )}
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
