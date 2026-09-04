/**
 * Раскладка раздела «Оплаты». Одна для обоих экранов, как в чатах:
 * на телефоне список занимает весь экран и прячется, когда открыта карточка;
 * на десктопе список слева, карточка справа.
 */

import { Download, Hash } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { DealStatus } from '@/entities/types'
import { ApiError } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { useMe } from '@/shared/hooks/useAuth'
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  InlineError,
  ListSkeleton,
  Segmented,
} from '@/shared/ui'
import { SectionActions } from '@/app/layout/SectionActions'
import { DealListItem } from '@/features/deals/components/DealListItem'
import { DealsSummaryTiles } from '@/features/deals/components/DealsSummaryTiles'
import { RequisiteBreakdown } from '@/features/deals/components/RequisiteBreakdown'
import {
  PeriodPicker,
  presetRange,
  type DateRange,
  type PeriodPreset,
} from '@/features/deals/components/PeriodPicker'
import { exportDeals, useDeals, useDealsSummary, type DealFilters } from '@/features/deals/queries'

type StatusFilter = DealStatus | 'all'

const STATUSES: StatusFilter[] = ['all', 'draft', 'awaiting', 'paid', 'cancelled', 'expired']

function isStatusFilter(value: string | null): value is StatusFilter {
  return value !== null && (STATUSES as string[]).includes(value)
}

export function PaymentsLayout({
  selectedId,
  children,
}: {
  selectedId: number | null
  children?: ReactNode
}) {
  // Ссылка из чата приходит с готовыми фильтрами: /payments?client_id=7&status=awaiting
  const [searchParams] = useSearchParams()
  // Доска: «в разделе оплат отражаются все оплаты за выбранный период
  // (по умолчанию — последний год)». Месяц здесь врал: плитка «ждут оплаты»
  // считает текущее состояние и показывала 5, а список за месяц — 3 строки.
  const [preset, setPreset] = useState<PeriodPreset>('year')
  const [range, setRange] = useState<DateRange>(() => presetRange('year'))
  const [status, setStatus] = useState<StatusFilter>(() => {
    const fromUrl = searchParams.get('status')
    return isStatusFilter(fromUrl) ? fromUrl : 'all'
  })
  const [clientInput, setClientInput] = useState(() => searchParams.get('client_id') ?? '')
  // ТЗ п. 4.6: переход из чата приносит канал — список сужается до него.
  const accountId = Number(searchParams.get('account_id')) || null
  const [clientQuery, setClientQuery] = useState(clientInput)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setClientQuery(clientInput.trim()), 300)
    return () => clearTimeout(timer)
  }, [clientInput])

  const clientIdInvalid = clientQuery.length > 0 && !/^\d+$/.test(clientQuery)
  const clientId = clientIdInvalid || clientQuery.length === 0 ? null : Number(clientQuery)

  // Плитки описывают весь период, поэтому статус в них не участвует.
  const periodFilters: DealFilters = useMemo(
    () => ({ dateFrom: range.from, dateTo: range.to, clientId, accountId }),
    [range.from, range.to, clientId, accountId],
  )
  const listFilters: DealFilters = useMemo(
    () => ({ ...periodFilters, status: status === 'all' ? null : status }),
    [periodFilters, status],
  )

  const me = useMe()
  const summary = useDealsSummary(periodFilters)
  const list = useDeals(listFilters)
  const deals = useMemo(() => list.data?.pages.flatMap((page) => page.items) ?? [], [list.data])

  function changePreset(next: PeriodPreset) {
    setPreset(next)
    setRange((current) => presetRange(next, current))
  }

  async function runExport() {
    setExportError(null)
    setExporting(true)
    try {
      await exportDeals(listFilters)
    } catch (cause) {
      setExportError(
        cause instanceof ApiError ? cause.message : 'Не удалось выгрузить отчёт по оплатам',
      )
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-1">
      <section
        className={cn(
          'flex min-h-0 w-full flex-col border-line desk:w-96 desk:shrink-0 desk:border-r',
          selectedId !== null && 'hidden desk:flex',
        )}
      >
        <header className="flex shrink-0 flex-col gap-3 border-b border-line px-4 pb-3 pt-4">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-lg font-semibold text-ink">Оплаты</h1>
            <SectionActions className="ml-auto" />
            <Button
              size="sm"
              variant="secondary"
              loading={exporting}
              onClick={() => void runExport()}
            >
              <Download className="size-4" aria-hidden />
              Выгрузить
            </Button>
          </div>

          <PeriodPicker
            preset={preset}
            range={range}
            onPresetChange={changePreset}
            onRangeChange={setRange}
          />

          <Input
            value={clientInput}
            inputMode="numeric"
            onChange={(e) => setClientInput(e.target.value)}
            placeholder="Фильтр по id клиента"
            invalid={clientIdInvalid}
            leading={<Hash className="size-4" aria-hidden />}
            trailing={
              clientInput ? (
                <button
                  onClick={() => setClientInput('')}
                  className="text-micro text-ink-faint transition-colors hover:text-ink"
                >
                  Сбросить
                </button>
              ) : undefined
            }
          />
          {clientIdInvalid && (
            <span className="text-xs text-danger">
              Id клиента — только цифры. Например: 128.
            </span>
          )}

          {exportError && <InlineError message={exportError} onRetry={() => void runExport()} />}

          <DealsSummaryTiles
            summary={summary.data}
            isLoading={summary.isLoading}
            error={summary.error}
            onRetry={() => void summary.refetch()}
          />

          {me.is_admin && <RequisiteBreakdown filters={periodFilters} />}

          <Segmented
            value={status}
            onChange={setStatus}
            options={[
              { value: 'all', label: 'Все' },
              { value: 'awaiting', label: 'Ждут оплаты', count: summary.data?.awaiting_count },
              { value: 'paid', label: 'Оплачено', count: summary.data?.paid_count },
              { value: 'cancelled', label: 'Отменено' },
              { value: 'expired', label: 'Истекло' },
            ]}
          />
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {list.isLoading ? (
            <ListSkeleton rows={8} />
          ) : list.error ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : deals.length === 0 ? (
            <EmptyState
              title="Оплат за период нет"
              hint={
                clientId !== null
                  ? `По клиенту с id ${clientId} за выбранный период оплат не нашлось.`
                  : status === 'all'
                    ? 'Оплата создаётся из чата с клиентом — кнопка «Оплата» в шапке чата.'
                    : 'Смените статус или период.'
              }
            />
          ) : (
            <>
              {deals.map((deal) => (
                <DealListItem key={deal.id} deal={deal} active={deal.id === selectedId} />
              ))}
              {list.hasNextPage && (
                <div className="p-3">
                  <Button
                    variant="secondary"
                    fullWidth
                    loading={list.isFetchingNextPage}
                    onClick={() => void list.fetchNextPage()}
                  >
                    Показать ещё
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </section>

      <section
        className={cn(
          'flex min-h-0 min-w-0 flex-1 flex-col',
          selectedId === null && 'hidden desk:flex',
        )}
      >
        {children ?? (
          <EmptyState
            title="Выберите оплату"
            hint="Слева список сделок за период. Нажмите на строку, чтобы открыть карточку."
          />
        )}
      </section>
    </div>
  )
}
