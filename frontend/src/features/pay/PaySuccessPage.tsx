import { CheckCircle2 } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { money, parseMoney } from '@/shared/lib/format'

/**
 * SuccessURL Робокассы. Подписан Паролем #1 — тем же, что участвует в самой
 * ссылке на оплату, то есть фактически публичным. Поэтому страница ничего
 * не пишет в базу и не дёргает бэкенд: только читает query-параметры для
 * текста. Статус сделки меняет исключительно ResultURL (см. deal_service.py,
 * confirm_paid_by_provider). Без авторизации — клиент не сотрудник CRM.
 */
export function PaySuccessPage() {
  const [params] = useSearchParams()
  const invId = params.get('InvId')
  const outSum = params.get('OutSum')
  const amountKopecks = outSum ? parseMoney(outSum) : null

  return (
    <div className="flex h-dvh items-center justify-center px-5">
      <div className="flex w-full max-w-sm flex-col items-center gap-4 text-center">
        <CheckCircle2 className="size-12 text-success" aria-hidden />
        <h1 className="text-2xl font-semibold text-ink">Оплата принята</h1>
        <p className="text-sm text-ink-muted">
          {amountKopecks
            ? `Спасибо! Платёж на ${money(amountKopecks)} получен.`
            : 'Спасибо! Платёж получен.'}
        </p>
        {invId && <p className="text-xs text-ink-faint">Заказ №{invId}</p>}
        <p className="text-xs text-ink-faint">
          Менеджер увидит поступление автоматически — можно закрыть эту страницу
          и вернуться в чат с ним.
        </p>
      </div>
    </div>
  )
}
