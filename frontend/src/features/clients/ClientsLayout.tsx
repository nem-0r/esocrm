/**
 * Раскладка раздела «Клиенты»: слева список, справа карточка.
 *
 * Одна и та же разметка на двух раскладках — как в «Чатах». На телефоне видно
 * что-то одно: список либо открытая карточка. С 1024px обе колонки рядом.
 */

import { Download, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { cn } from '@/shared/lib/cn'
import {
  Button,
  EmptyState,
  ErrorState,
  InlineError,
  Input,
  ListSkeleton,
  Segmented,
} from '@/shared/ui'
import { SectionActions } from '@/app/layout/SectionActions'
import { ClientListItem } from '@/features/clients/components/ClientListItem'
import { errorText } from '@/features/clients/clientText'
import { useClients, useExportClients, type ClientSort } from '@/features/clients/queries'

export function ClientsLayout({
  selectedId,
  children,
}: {
  selectedId: number | null
  children?: React.ReactNode
}) {
  const [sort, setSort] = useState<ClientSort>('last_contact')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

  const list = useClients({ q: debounced, sort })
  const exportCsv = useExportClients()

  const clients = useMemo(
    () => list.data?.pages.flatMap((page) => page.items) ?? [],
    [list.data],
  )

  // Сервер отдаёт общее число не всегда. Пока страницы не дочитаны, показывать
  // количество загруженных строк было бы враньём — тогда не показываем ничего.
  const total =
    list.data?.pages[0]?.total ?? (list.data && !list.hasNextPage ? clients.length : null)

  return (
    <div className="flex min-h-0 flex-1">
      {/* Список. На телефоне скрыт, когда открыта карточка. */}
      <section
        className={cn(
          'flex min-h-0 w-full flex-col border-line desk:w-[310px] desk:shrink-0 desk:border-r xl:w-[360px]',
          selectedId !== null && 'hidden desk:flex',
        )}
      >
        <header className="shrink-0 border-b border-line px-4 pb-3 pt-4">
          <div className="flex items-center justify-between gap-3">
            <h1 className="flex min-w-0 items-baseline gap-2 text-lg font-semibold text-ink">
              Клиенты
              {total !== null && (
                <span className="tnum shrink-0 text-sm font-normal text-ink-faint">{total}</span>
              )}
            </h1>
            <SectionActions />
            <Button
              variant="secondary"
              size="sm"
              className="shrink-0"
              title="Выгрузка в CSV за последний год"
              loading={exportCsv.isPending}
              onClick={() => exportCsv.mutate()}
            >
              <Download className="size-4" aria-hidden />
              Выгрузить
            </Button>
          </div>

          <Input
            className="mt-3"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Имя, телефон или id клиента"
            leading={<Search className="size-4" aria-hidden />}
          />

          <Segmented
            className="mt-3"
            value={sort}
            onChange={setSort}
            options={[
              { value: 'last_contact', label: 'По дате контакта' },
              { value: 'amount', label: 'По сумме оплат' },
            ]}
          />

          <p className="mt-2 text-micro text-ink-faint">Выгрузка — за последний год.</p>

          {exportCsv.error && (
            <div className="mt-2">
              <InlineError
                message={errorText(exportCsv.error)}
                onRetry={() => exportCsv.mutate()}
              />
            </div>
          )}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {list.isLoading ? (
            <ListSkeleton rows={8} />
          ) : list.error ? (
            <ErrorState error={list.error} onRetry={() => void list.refetch()} />
          ) : clients.length === 0 ? (
            <EmptyState
              title={debounced ? `По запросу «${debounced}» ничего не найдено` : 'Клиентов пока нет'}
              hint={
                debounced
                  ? 'Проверьте написание или очистите поиск.'
                  : 'Карточка заводится сама, когда клиент пишет первым.'
              }
            />
          ) : (
            <>
              {clients.map((client) => (
                <ClientListItem
                  key={client.id}
                  client={client}
                  active={client.id === selectedId}
                />
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

      {/* Карточка клиента */}
      <section
        className={cn(
          'flex min-h-0 min-w-0 flex-1 flex-col',
          selectedId === null && 'hidden desk:flex',
        )}
      >
        {children ?? (
          <EmptyState
            title="Выберите клиента"
            hint="Слева список. В карточке — оплаты, материалы, чаты и заметки."
          />
        )}
      </section>
    </div>
  )
}
