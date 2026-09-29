import * as Menu from '@radix-ui/react-dropdown-menu'
import { CheckSquare, Copy, Download, Forward, ImageDown, MoreHorizontal, Pencil } from 'lucide-react'
import type { ReactNode } from 'react'

import { cn } from '@/shared/lib/cn'

export interface MessageActions {
  onCopyText?: () => void
  onCopyImage?: () => void
  // Один файл — один пункт «Скачать». Несколько (альбом) — по пункту на
  // файл, с именем в подписи, иначе не разобрать, что именно скачивается.
  downloads?: { label: string; run: () => void }[]
  onForward?: () => void
  onSelect?: () => void
  onEdit?: () => void
}

/**
 * Меню «⋯» у сообщения. Пункт появляется только если действие возможно
 * именно для этого сообщения — «ни одной кнопки-обманки».
 */
export function MessageMenu({ actions, className }: { actions: MessageActions; className?: string }) {
  const items: { key: string; label: string; icon: ReactNode; run: () => void }[] = []
  if (actions.onCopyText)
    items.push({ key: 'copy', label: 'Копировать текст', icon: <Copy className="size-4" />, run: actions.onCopyText })
  if (actions.onCopyImage)
    items.push({
      key: 'image',
      label: 'Копировать изображение',
      icon: <ImageDown className="size-4" />,
      run: actions.onCopyImage,
    })
  actions.downloads?.forEach((file, index) =>
    items.push({
      key: `download-${index}`,
      label: file.label,
      icon: <Download className="size-4" />,
      run: file.run,
    }),
  )
  if (actions.onForward)
    items.push({ key: 'forward', label: 'Переслать', icon: <Forward className="size-4" />, run: actions.onForward })
  if (actions.onSelect)
    items.push({ key: 'select', label: 'Выбрать', icon: <CheckSquare className="size-4" />, run: actions.onSelect })
  if (actions.onEdit)
    items.push({ key: 'edit', label: 'Изменить', icon: <Pencil className="size-4" />, run: actions.onEdit })
  if (items.length === 0) return null

  return (
    <Menu.Root modal={false}>
      <Menu.Trigger asChild>
        <button
          type="button"
          aria-label="Действия с сообщением"
          className={cn(
            'relative flex size-5 items-center justify-center rounded text-ink-faint transition-colors hover:text-ink',
            'before:absolute before:-inset-2 before:content-[""]',
            className,
          )}
        >
          <MoreHorizontal className="size-4" aria-hidden />
        </button>
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={4}
          className="z-50 min-w-48 rounded-md border border-line-strong bg-surface-raised p-1 shadow-sheet"
        >
          {items.map((item) => (
            <Menu.Item
              key={item.key}
              onSelect={item.run}
              className="flex cursor-pointer items-center gap-2.5 rounded px-2.5 py-2 text-sm text-ink outline-none data-[highlighted]:bg-line"
            >
              <span className="text-ink-muted" aria-hidden>
                {item.icon}
              </span>
              {item.label}
            </Menu.Item>
          ))}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  )
}
