import { AlertTriangle, Info, OctagonAlert, X } from 'lucide-react'
import { useState } from 'react'

import type { SystemAlert } from '@/entities/types'
import { cn } from '@/shared/lib/cn'
import { useSystemStatus } from '@/features/system/queries'

const STYLE: Record<SystemAlert['level'], string> = {
  critical: 'bg-danger-soft text-danger',
  warning: 'bg-warning-soft text-warning',
  info: 'bg-accent-soft text-accent-text',
}

const ICON = { critical: OctagonAlert, warning: AlertTriangle, info: Info } as const

/**
 * Полоса над содержимым — только руководителю. Показывает то, что требует внимания
 * до аварии: на диске кончается место, шлюз Telegram не запущен, сервер сменил размер.
 * Тексты готовит сервер (`system_status.build`), здесь они только показываются.
 * Информационные и предупреждающие сообщения можно скрыть до перезагрузки страницы;
 * критические — нет: их нельзя пропустить.
 */
export function ServerAlertBanner({ enabled }: { enabled: boolean }) {
  const status = useSystemStatus(enabled)
  const [hidden, setHidden] = useState<string[]>([])
  const alerts = (status.data?.alerts ?? []).filter(
    (alert) => alert.level === 'critical' || !hidden.includes(alert.text),
  )
  if (!enabled || alerts.length === 0) return null

  return (
    <div role="region" aria-label="Состояние сервера" className="flex shrink-0 flex-col">
      {alerts.map((alert) => {
        const Icon = ICON[alert.level]
        return (
          <div
            key={alert.text}
            role={alert.level === 'critical' ? 'alert' : 'status'}
            className={cn('flex items-start gap-2 px-4 py-2 text-xs leading-relaxed', STYLE[alert.level])}
          >
            <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1">{alert.text}</span>
            {alert.level !== 'critical' && (
              <button
                type="button"
                aria-label="Скрыть до перезагрузки страницы"
                title="Скрыть до перезагрузки страницы"
                onClick={() => setHidden((prev) => [...prev, alert.text])}
                className="shrink-0 rounded p-0.5 opacity-70 transition-opacity hover:opacity-100"
              >
                <X className="size-4" aria-hidden />
              </button>
            )}
          </div>
        )
      })}
    </div>
  )
}
