import { XCircle } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

/**
 * FailURL Робокассы. Как и SuccessURL — публичный редирект без проверяемой
 * подписи, поэтому страница только показывает текст и не меняет сделку.
 * Ссылка на оплату остаётся рабочей: клиент может попробовать ещё раз тем
 * же адресом, пока сделка не истекла.
 */
export function PayFailPage() {
  const [params] = useSearchParams()
  const invId = params.get('InvId')

  return (
    <div className="flex h-dvh items-center justify-center px-5">
      <div className="flex w-full max-w-sm flex-col items-center gap-4 text-center">
        <XCircle className="size-12 text-danger" aria-hidden />
        <h1 className="text-2xl font-semibold text-ink">Оплата не прошла</h1>
        <p className="text-sm text-ink-muted">
          Платёж не был завершён — деньги не списаны. Попробуйте ещё раз по той же
          ссылке или напишите менеджеру в чат.
        </p>
        {invId && <p className="text-xs text-ink-faint">Заказ №{invId}</p>}
      </div>
    </div>
  )
}
