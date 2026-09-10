/**
 * Поступления в разрезе реквизитов — то, с чем сверяют банковскую выписку.
 *
 * В выписке видно счёт, сумму и комментарий с кодом платежа; здесь те же деньги
 * со стороны CRM. Расхождение означает либо оплату, которую не отметили, либо
 * деньги, пришедшие не туда, куда выставляли счёт.
 *
 * Показывается только руководителю: менеджеру чужие поступления не нужны,
 * а сумма по компании — не его данные.
 */

import { useState } from 'react'
import { ChevronDown } from 'lucide-react'

import { money, plural } from '@/shared/lib/format'
import { InlineError, SectionTitle, Skeleton } from '@/shared/ui'
import { useDealsByRequisite, type DealFilters } from '@/features/deals/queries'

export function RequisiteBreakdown({ filters }: { filters: DealFilters }) {
  const [open, setOpen] = useState(false)
  const query = useDealsByRequisite(filters, open)

  const rows = query.data ?? []
  const total = rows.reduce((sum, row) => sum + row.amount, 0)

  return (
    <div className="rounded-lg border border-line bg-surface-raised">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-3 text-left"
      >
        <SectionTitle>Поступления по реквизитам</SectionTitle>
        <ChevronDown
          className={`size-4 shrink-0 text-ink-faint transition-transform ${open ? 'rotate-180' : ''}`}
          aria-hidden
        />
      </button>

      {open && (
        <div className="flex flex-col gap-2 px-3 pb-3">
          {query.isLoading ? (
            <>
              <Skeleton className="h-9 rounded" />
              <Skeleton className="h-9 rounded" />
            </>
          ) : query.error ? (
            <InlineError
              message="Не удалось посчитать поступления"
              onRetry={() => void query.refetch()}
            />
          ) : rows.length === 0 ? (
            <p className="text-xs text-ink-muted">
              За выбранный период подтверждённых оплат нет.
            </p>
          ) : (
            <>
              <ul className="flex flex-col gap-1.5">
                {rows.map((row) => (
                  <li
                    key={row.requisite_id ?? row.title}
                    className="flex items-center justify-between gap-3 rounded bg-surface px-2.5 py-2"
                  >
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate text-sm text-ink">{row.title}</span>
                      <span className="text-micro text-ink-faint">
                        {row.country ? `${row.country} · ` : ''}
                        {row.count} {plural(row.count, 'оплата', 'оплаты', 'оплат')}
                      </span>
                    </div>
                    <span className="shrink-0 text-sm tabular-nums text-ink">
                      {money(row.amount)}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="flex items-center justify-between border-t border-line pt-2 text-sm">
                <span className="text-ink-muted">Всего за период</span>
                <span className="tabular-nums text-ink">{money(total)}</span>
              </div>
              <p className="text-micro text-ink-faint">
                Сравните с выпиской по каждому счёту — сверка идёт по приложенному чеку.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  )
}
