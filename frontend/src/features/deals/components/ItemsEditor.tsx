import { Check, Plus, Tag, Trash2 } from 'lucide-react'
import { useMemo, useState, type KeyboardEvent } from 'react'

import type { Service } from '@/entities/types'
import { amountInput, newItemDraft, type ItemDraft } from '@/features/deals/lib'
import { useServices } from '@/features/services/queries'
import { cn } from '@/shared/lib/cn'
import { money, parseMoney } from '@/shared/lib/format'
import { Button, Input } from '@/shared/ui'

const MAX_SHOWN = 8

/**
 * Состав сделки: услуги и цены. Одинаковый в окне создания оплаты и в правке
 * сделки — чтобы менеджер не учил два разных поведения.
 *
 * Название — поле с перечнем из справочника «Услуги и цены»: начали печатать —
 * список сузился; выбрали — подставились название и цена по прайсу. Своё
 * название вписать можно: справочник помогает, а не запрещает (на доске Miro
 * «менеджеры вводят название и стоимость вручную» — это остаётся возможным).
 */
export function ItemsEditor({
  items,
  onChange,
}: {
  items: ItemDraft[]
  onChange: (items: ItemDraft[]) => void
}) {
  const services = useServices()
  const catalog = useMemo(() => services.data ?? [], [services.data])

  function patch(index: number, update: Partial<ItemDraft>) {
    onChange(items.map((item, i) => (i === index ? { ...item, ...update } : item)))
  }

  return (
    <section className="flex flex-col gap-2">
      <span className="text-label uppercase tracking-wide text-ink-faint">Услуги · {items.length}</span>
      {items.map((item, index) => {
        const linked = catalog.find((service) => service.id === item.serviceId) ?? null
        const amount = parseMoney(item.amount)
        return (
          <div key={item.key} className="flex flex-col gap-2 rounded-md bg-surface-raised p-3">
            <div className="flex items-start gap-2">
              <ServiceNameField
                catalog={catalog}
                item={item}
                onPick={(service) =>
                  patch(index, {
                    name: service.name,
                    serviceId: service.id,
                    // Цену подставляем, только если она есть в справочнике:
                    // «по договорённости» — сумму впишут руками.
                    amount: service.price !== null ? amountInput(service.price) : item.amount,
                  })
                }
                onType={(name) => {
                  // Своё название — это уже не услуга из справочника, если только
                  // не совпало с ней буква в букву (тогда связываем сами).
                  const exact = catalog.find(
                    (service) => service.name.toLowerCase() === name.trim().toLowerCase(),
                  )
                  patch(index, { name, serviceId: exact ? exact.id : null })
                }}
              />
              {items.length > 1 && (
                <button
                  type="button"
                  aria-label="Убрать услугу"
                  onClick={() => onChange(items.filter((_, i) => i !== index))}
                  className="flex size-10 shrink-0 items-center justify-center rounded text-ink-faint transition-colors hover:bg-surface hover:text-danger"
                >
                  <Trash2 className="size-4" aria-hidden />
                </button>
              )}
            </div>
            <Input
              className="bg-surface"
              inputMode="decimal"
              value={item.amount}
              placeholder="Стоимость, ₽"
              onChange={(event) => patch(index, { amount: event.target.value })}
              trailing={
                <span className="tnum text-micro text-ink-faint">
                  {amount ? money(amount) : ''}
                </span>
              }
            />
            {linked && linked.price !== null && amount !== null && amount !== linked.price && (
              <span className="text-micro text-ink-faint">По прайсу {money(linked.price)}</span>
            )}
          </div>
        )
      })}
      <Button
        variant="secondary"
        size="sm"
        className="self-start"
        onClick={() => onChange([...items, newItemDraft()])}
      >
        <Plus className="size-4" aria-hidden />
        Добавить услугу
      </Button>
    </section>
  )
}

function ServiceNameField({
  catalog,
  item,
  onPick,
  onType,
}: {
  catalog: Service[]
  item: ItemDraft
  onPick: (service: Service) => void
  onType: (name: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)

  const query = item.name.trim().toLowerCase()
  const options = useMemo(() => {
    // Выбранная услуга уже в поле — список не мешает, пока не начнут печатать.
    const matches = catalog.filter(
      (service) => !query || service.name.toLowerCase().includes(query),
    )
    return matches.slice(0, MAX_SHOWN)
  }, [catalog, query])

  const showList = open && catalog.length > 0 && options.length > 0 && !(item.serviceId && options.length === 1)

  function choose(service: Service) {
    onPick(service)
    setOpen(false)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (!showList) {
      if (event.key === 'ArrowDown' && catalog.length) setOpen(true)
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((index) => (index + 1) % options.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => (index - 1 + options.length) % options.length)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      choose(options[Math.min(active, options.length - 1)])
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <Input
        className="bg-surface"
        value={item.name}
        placeholder={catalog.length ? 'Выберите услугу или впишите свою' : 'Название услуги'}
        onFocus={() => {
          setOpen(true)
          setActive(0)
        }}
        // Задержка — чтобы нажатие на пункт списка успело сработать до закрытия.
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onChange={(event) => {
          onType(event.target.value)
          setOpen(true)
          setActive(0)
        }}
        onKeyDown={onKeyDown}
        role="combobox"
        aria-expanded={showList}
        aria-autocomplete="list"
        leading={item.serviceId ? <Tag className="size-3.5 text-accent-text" aria-label="из справочника" /> : undefined}
      />
      {showList && (
        <ul role="listbox" className="flex max-h-56 flex-col overflow-y-auto rounded-md border border-line-strong bg-surface py-1">
          {options.map((service, index) => (
            <li key={service.id} role="option" aria-selected={service.id === item.serviceId}>
              <button
                type="button"
                // mousedown, а не click: поле ввода теряет фокус раньше, чем
                // придёт click, и список закрылся бы до выбора.
                onMouseDown={(event) => {
                  event.preventDefault()
                  choose(service)
                }}
                onMouseEnter={() => setActive(index)}
                className={cn(
                  'flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors',
                  index === active ? 'bg-surface-raised' : '',
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-ink">{service.name}</span>
                  {service.description && (
                    <span className="block truncate text-micro text-ink-faint">{service.description}</span>
                  )}
                </span>
                <span className="tnum shrink-0 text-xs text-ink-muted">
                  {service.price !== null ? money(service.price) : 'по договорённости'}
                </span>
                {service.id === item.serviceId && (
                  <Check className="size-4 shrink-0 text-accent-text" aria-hidden />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
