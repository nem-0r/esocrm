/**
 * Ссылка для сотрудника: приглашение или сброс пароля.
 *
 * Отправки писем в системе нет, и делать вид, что письмо ушло, нельзя.
 * Поэтому показываем ссылку целиком, даём её скопировать и прямо пишем,
 * что передать её сотруднику нужно самому. Подпись и пояснение можно
 * заменить — для сброса пароля они другие (D-38).
 */

import { Check, Copy } from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/shared/ui'
import { inviteLink } from '@/features/profile/lib'

export function InviteLink({
  url,
  label = 'Ссылка-приглашение',
  note = 'Письма пока не отправляются — передайте ссылку сотруднику сами',
}: {
  url: string
  label?: string
  note?: string
}) {
  const full = inviteLink(url)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(false), 2000)
    return () => clearTimeout(timer)
  }, [copied])

  async function copy() {
    try {
      await navigator.clipboard.writeText(full)
      setCopied(true)
    } catch {
      // Буфер обмена недоступен — ссылка видна целиком, её можно выделить руками.
      setCopied(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-accent-line bg-accent-soft p-3">
      <p className="text-label uppercase tracking-wide text-ink-faint">{label}</p>
      <p className="break-all text-sm text-accent-text">{full}</p>
      <div className="flex items-center gap-2">
        <Button variant="secondary" size="sm" onClick={() => void copy()}>
          {copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
          {copied ? 'Скопировано' : 'Скопировать'}
        </Button>
      </div>
      <p className="text-xs text-ink-muted">{note}</p>
    </div>
  )
}
