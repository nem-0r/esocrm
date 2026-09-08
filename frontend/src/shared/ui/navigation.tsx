import { ArrowLeft } from 'lucide-react'
import { useLocation, useNavigate } from 'react-router-dom'

import { cn } from '@/shared/lib/cn'

/**
 * Стрелка «назад» — настоящий шаг назад по истории браузера, а не жёстко
 * заданный маршрут. Куда бы ни зашли (из списка, из карточки клиента, из
 * поиска), ведёт туда, откуда реально пришли — как в Telegram.
 *
 * `fallback` нужен только на случай, если настоящей истории нет: прямая
 * ссылка на этот экран или перезагрузка страницы. React Router помечает
 * самую первую запись в истории вкладки как "default" — до неё в приложении
 * ничего не было, и `navigate(-1)` в этом случае увёл бы из CRM вообще,
 * а не назад по цепочке экранов.
 */
export function BackButton({
  fallback,
  label = 'Назад',
  className,
}: {
  fallback: string
  label?: string
  className?: string
}) {
  const navigate = useNavigate()
  const location = useLocation()

  function handleClick() {
    if (location.key === 'default') {
      navigate(fallback, { replace: true })
    } else {
      navigate(-1)
    }
  }

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={handleClick}
      className={cn(
        'flex size-9 shrink-0 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink',
        className,
      )}
    >
      <ArrowLeft className="size-5" aria-hidden />
    </button>
  )
}
