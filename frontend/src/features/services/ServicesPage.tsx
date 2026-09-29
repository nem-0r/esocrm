import { History, Pencil, Plus, Sparkles, Tags, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { Service, ServiceSuggestion } from '@/entities/types'
import { ProfileScreen } from '@/features/profile/components/ProfileScreen'
import { errorMessage } from '@/features/profile/lib'
import {
  useAdminServices,
  useCreateService,
  useDeleteService,
  useServiceSuggestions,
  useUpdateService,
} from '@/features/services/queries'
import { dateShort, money, parseMoney, plural } from '@/shared/lib/format'
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  InlineError,
  Input,
  ListSkeleton,
  SectionTitle,
  Sheet,
  Switch,
  Textarea,
  toast,
} from '@/shared/ui'

type Draft = { service: Service | null; suggestion?: ServiceSuggestion }

/** Копейки → текст поля ввода: «4500» или «4500,50» — копейки не теряем. */
function priceInput(kopecks: number): string {
  const whole = Math.trunc(kopecks / 100)
  const rest = Math.abs(kopecks % 100)
  return rest ? `${whole},${String(rest).padStart(2, '0')}` : String(whole)
}

/**
 * Справочник услуг: то, что менеджер выбирает в окне оплаты.
 *
 * Цена — подсказка: в сделке её можно поправить, а в самой сделке остаётся
 * снимок цены на момент продажи. Поэтому новая цена здесь не меняет ни счета,
 * уже отправленные клиентам, ни статистику прошлых месяцев.
 *
 * «Подсказки из истории» — названия, которые менеджеры вписывали в сделки
 * вручную: по ним видно, что реально продаётся, — добавить их можно одной кнопкой.
 */
export function ServicesPage() {
  const services = useAdminServices()
  const suggestions = useServiceSuggestions(true)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [removing, setRemoving] = useState<Service | null>(null)
  const remove = useDeleteService()
  // «Снять с продажи» вместо удаления — та же мутация, что в форме услуги,
  // отдельный экземпляр, чтобы не путать её состояние загрузки с формой.
  const deactivate = useUpdateService()
  const [removeError, setRemoveError] = useState<string | null>(null)
  const rows = services.data ?? []
  const hints = suggestions.data ?? []

  return (
    <ProfileScreen
      title="Услуги и цены"
      subtitle="Перечень в окне оплаты. Цену в сделке менеджер может поправить"
      backTo="/profile"
      action={
        <Button size="sm" onClick={() => setDraft({ service: null })}>
          <Plus className="size-4" aria-hidden />
          Добавить
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {services.isLoading ? (
          <ListSkeleton rows={4} />
        ) : services.error ? (
          <ErrorState error={services.error} onRetry={() => void services.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Справочник пуст"
            hint="Добавьте услуги — менеджеры будут выбирать их в окне оплаты, а не вписывать вручную."
            icon={<Tags className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((service) => (
              <li key={service.id}>
                <Card>
                  <div className="flex items-start gap-3">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-ink">{service.name}</span>
                        {!service.is_active && <Badge tone="neutral">снята с продажи</Badge>}
                      </div>
                      <span className="tnum text-sm text-ink-muted">
                        {service.price !== null ? money(service.price) : 'цена по договорённости'}
                      </span>
                      {service.description && (
                        <span className="text-xs text-ink-faint">{service.description}</span>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <button
                        onClick={() => setDraft({ service })}
                        title="Изменить"
                        aria-label={`Изменить «${service.name}»`}
                        className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
                      >
                        <Pencil className="size-4" aria-hidden />
                      </button>
                      <button
                        onClick={() => {
                          setRemoveError(null)
                          setRemoving(service)
                        }}
                        title="Удалить"
                        aria-label={`Удалить «${service.name}»`}
                        className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-danger-soft hover:text-danger"
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </button>
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        )}

        {(suggestions.isLoading || hints.length > 0 || suggestions.error) && (
          <Card>
            <SectionTitle>
              <span className="flex items-center gap-2">
                <History className="size-4 text-ink-faint" aria-hidden />
                Подсказки из истории
              </span>
            </SectionTitle>
            <p className="-mt-1 text-xs text-ink-faint">
              Названия, которые менеджеры вписывали в сделки за год вручную, — и их типичная цена.
            </p>
            {suggestions.isLoading ? (
              <ListSkeleton rows={2} />
            ) : suggestions.error ? (
              <InlineError message="Не удалось загрузить подсказки" onRetry={() => void suggestions.refetch()} />
            ) : (
              <ul className="flex flex-col gap-1.5">
                {hints.map((hint) => (
                  <li
                    key={hint.name}
                    className="flex items-center gap-3 rounded-md bg-surface-raised px-3 py-2"
                  >
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm text-ink">{hint.name}</span>
                      <span className="text-micro text-ink-faint">
                        {hint.uses} {plural(hint.uses, 'раз', 'раза', 'раз')}
                        {hint.typical_price !== null ? ` · обычно ${money(hint.typical_price)}` : ''} ·
                        последний {dateShort(hint.last_used_at)}
                      </span>
                    </span>
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => setDraft({ service: null, suggestion: hint })}
                    >
                      <Sparkles className="size-4" aria-hidden />
                      Добавить
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}
      </div>

      <ServiceSheet draft={draft} onClose={() => setDraft(null)} />

      {/* Свой Sheet вместо ConfirmDialog: нужна третья кнопка («Снять с
          продажи») и место для ошибки удаления ВНУТРИ окна, а не под списком
          за его затемнением, где её раньше не было видно (аудит №32, №33). */}
      <Sheet
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title="Удалить услугу"
        description={
          removing
            ? `«${removing.name}» пропадёт из окна оплаты. Уже выставленные счета и статистика прошлых продаж не изменятся.`
            : ''
        }
        className="desk:w-[440px]"
        footer={
          <div className="flex flex-col gap-2">
            {removing?.is_active && (
              <Button
                variant="secondary"
                fullWidth
                disabled={remove.isPending}
                loading={deactivate.isPending}
                onClick={async () => {
                  if (!removing) return
                  setRemoveError(null)
                  try {
                    await deactivate.mutateAsync({ id: removing.id, is_active: false })
                    toast(`Услуга «${removing.name}» снята с продажи`)
                    setRemoving(null)
                  } catch (cause) {
                    setRemoveError(errorMessage(cause))
                  }
                }}
              >
                Снять с продажи
              </Button>
            )}
            <div className="flex gap-2">
              <Button variant="secondary" fullWidth onClick={() => setRemoving(null)}>
                Отмена
              </Button>
              <Button
                variant="danger"
                fullWidth
                disabled={deactivate.isPending}
                loading={remove.isPending}
                onClick={async () => {
                  if (!removing) return
                  setRemoveError(null)
                  try {
                    await remove.mutateAsync(removing.id)
                    toast(`Услуга «${removing.name}» удалена`)
                    setRemoving(null)
                  } catch (cause) {
                    setRemoveError(errorMessage(cause))
                  }
                }}
              >
                Удалить
              </Button>
            </div>
          </div>
        }
      >
        <div className="flex flex-col gap-3">
          {removing?.is_active && (
            <p className="text-xs text-ink-faint">
              Если услугу могут вернуть — не обязательно удалять: «Снять с продажи» скроет
              её из окна оплаты, но оставит в справочнике, и её можно будет включить обратно.
            </p>
          )}
          {removeError && <InlineError message={removeError} />}
        </div>
      </Sheet>
    </ProfileScreen>
  )
}

function ServiceSheet({ draft, onClose }: { draft: Draft | null; onClose: () => void }) {
  const open = draft !== null
  const service = draft?.service ?? null
  const create = useCreateService()
  const update = useUpdateService()
  const pending = create.isPending || update.isPending

  const [name, setName] = useState('')
  const [price, setPrice] = useState('')
  const [description, setDescription] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Каждое открытие — заново из актуальных данных: иначе форма хранила бы
  // правки предыдущей услуги.
  const key = draft ? (service?.id ?? `new:${draft.suggestion?.name ?? ''}`) : null
  const [loadedKey, setLoadedKey] = useState<string | number | null>(null)
  if (open && key !== loadedKey) {
    setLoadedKey(key)
    const suggestedPrice = draft?.suggestion?.typical_price ?? null
    setName(service?.name ?? draft?.suggestion?.name ?? '')
    const initialPrice = service ? service.price : suggestedPrice
    setPrice(initialPrice !== null && initialPrice !== undefined ? priceInput(initialPrice) : '')
    setDescription(service?.description ?? '')
    setIsActive(service?.is_active ?? true)
    setError(null)
  }
  if (!open && loadedKey !== null) setLoadedKey(null)

  const parsedPrice = price.trim() ? parseMoney(price) : null
  const priceInvalid = price.trim().length > 0 && parsedPrice === null
  const canSubmit = name.trim().length > 0 && !priceInvalid

  async function submit() {
    setError(null)
    const input = {
      name: name.trim(),
      price: parsedPrice,
      description: description.trim() || null,
      is_active: isActive,
    }
    try {
      if (service) await update.mutateAsync({ id: service.id, ...input })
      else await create.mutateAsync(input)
      toast(service ? 'Услуга сохранена' : 'Услуга добавлена в справочник')
      onClose()
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={service ? 'Изменить услугу' : 'Новая услуга'}
      description="Название менеджер увидит в окне оплаты, клиент — в счёте"
      footer={
        <Button fullWidth loading={pending} disabled={!canSubmit} onClick={() => void submit()}>
          {service ? 'Сохранить' : 'Добавить'}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Название" required>
          <Input
            value={name}
            maxLength={255}
            onChange={(event) => setName(event.target.value)}
            placeholder="Натальная карта"
          />
        </Field>
        <Field
          label="Цена, ₽"
          hint="Можно оставить пустой — тогда сумму менеджер впишет сам"
          error={priceInvalid ? 'Введите сумму больше нуля' : undefined}
        >
          <Input
            inputMode="decimal"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
            placeholder="4 500"
            invalid={priceInvalid}
          />
        </Field>
        <Field label="Подсказка менеджеру" hint="Необязательно: что входит, сроки">
          <Textarea
            rows={2}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Разбор по дате, времени и месту рождения, 2–3 дня"
          />
        </Field>
        <Switch
          checked={isActive}
          onChange={setIsActive}
          label="Продаётся"
          hint="Снятая с продажи услуга не предлагается в новых оплатах"
        />
        {error && <InlineError message={error} />}
      </div>
    </Sheet>
  )
}
