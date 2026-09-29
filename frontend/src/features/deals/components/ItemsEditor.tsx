import { Check, Info, Plus, Tag, Trash2 } from 'lucide-react'
import { useMemo, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'

import type { Service } from '@/entities/types'
import { amountInput, newItemDraft, normalizeServiceName, type ItemDraft } from '@/features/deals/lib'
import { useServices } from '@/features/services/queries'
import { useAuth } from '@/shared/hooks/useAuth'
import { cn } from '@/shared/lib/cn'
import { money, parseMoney } from '@/shared/lib/format'
import { Button, Input } from '@/shared/ui'

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
  const { isAdmin } = useAuth()
  const catalogEmpty = !services.isLoading && !services.isError && catalog.length === 0

  function patch(index: number, update: Partial<ItemDraft>) {
    onChange(items.map((item, i) => (i === index ? { ...item, ...update } : item)))
  }

  return (
    <section className="flex flex-col gap-2">
      <span className="text-label uppercase tracking-wide text-ink-faint">Услуги · {items.length}</span>
      {/* Пустой справочник — не поломка, но и списка не будет: говорим, почему и кто
          может это исправить, а не оставляем гадать. */}
      {catalogEmpty && (
        <span className="flex items-start gap-1.5 text-micro text-ink-faint">
          <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {isAdmin ? (
            <span>
              Справочник услуг пуст — список не появится, пока вы его не заполните:{' '}
              <Link to="/profile/services" className="text-accent-text underline-offset-4 hover:underline">
                Профиль → Услуги и цены
              </Link>
              . Пока название вписывается вручную.
            </span>
          ) : (
            <span>Справочник услуг пока пуст — впишите название вручную. Заполнить его может руководитель.</span>
          )}
        </span>
      )}
      {items.map((item, index) => {
        const linked = catalog.find((service) => service.id === item.serviceId) ?? null
        // Позиция ссылается на услугу, которой нет среди продающихся: значит
        // её сняли с продажи или удалили уже ПОСЛЕ того, как её выбрали в
        // этой сделке. Правку такой сделки сервер всё равно принимает —
        // позиция остаётся как есть (аудит №8), просто предупреждаем об этом.
        const missingService = item.serviceId !== null && !services.isLoading && !linked
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
                  // не совпало с ней буква в букву (без учёта регистра, пробелов
                  // по краям и е/ё — тогда связываем сами, и в статистике по
                  // услугам эта позиция не заведёт вторую строку, аудит №10).
                  const exact = catalog.find(
                    (service) => normalizeServiceName(service.name) === normalizeServiceName(name),
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
            {missingService && (
              <span className="flex items-center gap-1 text-micro text-ink-faint">
                <Info className="size-3.5 shrink-0" aria-hidden />
                Услуга снята с продажи — останется как есть
              </span>
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

  // «е»/«ё» — одна буква для поиска: «расчет» обязан находить «Расчёт…».
  const query = normalizeServiceName(item.name)
  const options = useMemo(
    // Выбранная услуга уже в поле — список не мешает, пока не начнут печатать.
    // Список не режем: он прокручивается (max-h-56), и справочник должен быть
    // виден целиком, а не первыми восемью по алфавиту (аудит №9).
    () => catalog.filter((service) => !query || normalizeServiceName(service.name).includes(query)),
    [catalog, query],
  )

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
