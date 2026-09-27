import { Square, Trash2 } from 'lucide-react'

import { playerTime } from '@/shared/lib/format'
import { IconButton } from '@/shared/ui'

/**
 * Полоса записи голосового — на месте поля ввода, как в Telegram:
 * красная точка, таймер, живой уровень громкости, «удалить» и «стоп».
 */
export function VoiceRecorderBar({
  seconds,
  level,
  requesting,
  onCancel,
  onStop,
}: {
  seconds: number
  level: number
  requesting: boolean
  onCancel: () => void
  onStop: () => void
}) {
  return (
    <div className="flex min-h-10 flex-1 items-center gap-3 rounded border border-danger/40 bg-danger-soft/40 px-3">
      <span className="relative flex size-3 shrink-0 items-center justify-center" aria-hidden>
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-danger/60" />
        <span className="relative inline-flex size-2.5 rounded-full bg-danger" />
      </span>
      <span className="tnum text-sm text-ink" aria-live="polite">
        {requesting ? 'Нужен доступ к микрофону…' : playerTime(seconds)}
      </span>
      {/* Живой уровень: видно, что микрофон действительно слышит. */}
      <span className="flex h-4 flex-1 items-center" aria-hidden>
        <span
          className="h-1.5 rounded-full bg-danger/70 transition-all duration-75"
          style={{ width: `${Math.max(4, Math.round(level * 100))}%` }}
        />
      </span>
      <IconButton label="Удалить запись" onClick={onCancel}>
        <Trash2 className="size-5" aria-hidden />
      </IconButton>
      <IconButton
        label="Остановить запись"
        onClick={onStop}
        disabled={requesting}
        className="text-danger hover:text-danger"
      >
        <Square className="size-4 fill-current" aria-hidden />
      </IconButton>
    </div>
  )
}
