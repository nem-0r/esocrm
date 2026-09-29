import { BarChart3, Contact, CornerUpRight, MapPin } from 'lucide-react'
import { Link } from 'react-router-dom'

import type { MessageMeta } from '@/entities/types'
import { dateFull } from '@/shared/lib/format'

/** «Переслано от …»: клиент переслал чужое сообщение или пост канала. */
export function ForwardedHeader({ meta }: { meta: MessageMeta }) {
  const from = meta.forwarded_from
  const inCrm = meta.forwarded
  if (!from && !inCrm) return null
  return (
    <div className="flex flex-col gap-0.5 border-l-2 border-accent-text/60 pl-2">
      {from && (
        <span className="flex items-center gap-1 text-micro text-accent-text">
          <CornerUpRight className="size-3" aria-hidden />
          Переслано от {from.name ?? 'скрытого отправителя'}
          {from.date ? ` · ${dateFull(from.date)}` : ''}
        </span>
      )}
      {inCrm && (
        <span className="text-micro text-ink-faint">
          Переслано из чата{' '}
          <Link to={`/chats/${inCrm.conversation_id}`} className="underline-offset-2 hover:underline">
            {inCrm.client_name ?? `№${inCrm.conversation_id}`}
          </Link>
          {inCrm.hide_sender
            ? ' · клиент видит без подписи «Переслано»'
            : ' · клиент видит «Переслано от …» с именем отправителя'}
        </span>
      )}
    </div>
  )
}

/**
 * Контакт, геопозиция, опрос — не пустой пузырь, а карточка. Текст с тем же
 * содержимым в сообщении уже есть (по нему работают поиск и список чатов),
 * карточка добавляет то, что удобно нажать: телефон, карту.
 */
export function MetaCard({ meta }: { meta: MessageMeta }) {
  if (meta.contact) {
    const { first_name, last_name, phone } = meta.contact
    const name = [first_name, last_name].filter(Boolean).join(' ') || 'Контакт'
    return (
      <div className="flex items-center gap-2.5 rounded bg-black/25 px-2.5 py-2">
        <Contact className="size-5 shrink-0 text-accent-text" aria-hidden />
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-sm text-ink">{name}</span>
          {phone && (
            <a href={`tel:${phone}`} className="tnum text-xs text-accent-text hover:underline">
              {phone}
            </a>
          )}
        </span>
      </div>
    )
  }
  if (meta.location) {
    const { lat, lon, title, address } = meta.location
    // Рынок российский — открываем Яндекс Карты с точкой.
    const href = `https://yandex.ru/maps/?pt=${lon},${lat}&z=16&l=map`
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="flex items-center gap-2.5 rounded bg-black/25 px-2.5 py-2 transition-colors hover:bg-black/40"
      >
        <MapPin className="size-5 shrink-0 text-accent-text" aria-hidden />
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-sm text-ink">{title ?? 'Геопозиция'}</span>
          <span className="truncate text-xs text-ink-faint">
            {address ?? `${lat.toFixed(5)}, ${lon.toFixed(5)}`} · открыть карту
          </span>
        </span>
      </a>
    )
  }
  if (meta.poll) {
    return (
      <div className="flex flex-col gap-1.5 rounded bg-black/25 px-2.5 py-2">
        <span className="flex items-center gap-1.5 text-sm text-ink">
          <BarChart3 className="size-4 shrink-0 text-accent-text" aria-hidden />
          {meta.poll.question}
        </span>
        <ul className="flex flex-col gap-1 pl-5">
          {meta.poll.options.map((option, index) => (
            <li key={index} className="text-xs text-ink-muted">
              {option}
            </li>
          ))}
        </ul>
      </div>
    )
  }
  return null
}

/** У карточек есть свой текст-заголовок: сам текст дублировать не нужно. */
export function hasMetaCard(meta: MessageMeta | null | undefined): boolean {
  return Boolean(meta && (meta.contact || meta.location || meta.poll))
}
