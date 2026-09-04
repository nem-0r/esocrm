import { Info, Plus, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { PaymentMethod } from '@/entities/types'
import { money, parseMoney } from '@/shared/lib/format'
import { useMe } from '@/shared/hooks/useAuth'
import { Button, Field, InlineError, Input, Select, Sheet, Textarea } from '@/shared/ui'
import {
  LINK_NOT_READY,
  draftTotal,
  itemsError,
  newItemDraft,
  toItemsPayload,
  type ItemDraft,
} from '@/features/deals/lib'
import { useCreateAndSendDeal, useRequisites } from '@/features/deals/queries'

/**
 * Создание оплаты. Открывается из чата и из карточки клиента.
 *
 * Счёт всегда уходит в конкретный чат — поэтому из карточки клиента, где чатов
 * может быть несколько, менеджер выбирает, в какой именно. Из чата выбирать
 * нечего, и переключатель не показывается.
 *
 * Способ «Ссылка» доступен, только если Робокасса настроена на сервере
 * (`me.robokassa_enabled`) — иначе кнопка отключена и подписана причиной,
 * а не притворяется рабочей.
 */
export function DealSheet({
  open,
  onOpenChange,
  conversationId,
  clientName,
  conversations,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversationId: number
  clientName: string
  conversations?: { id: number; label: string }[]
}) {
  const me = useMe()
  const requisites = useRequisites()
  const [convId, setConvId] = useState(conversationId)
  const createAndSend = useCreateAndSendDeal(convId)

  const [items, setItems] = useState<ItemDraft[]>([newItemDraft()])
  const [method, setMethod] = useState<PaymentMethod>('requisites')
  const [requisiteId, setRequisiteId] = useState<string | null>(null)
  const [introText, setIntroText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) {
      setItems([newItemDraft()])
      setMethod('requisites')
      setIntroText('')
      setError(null)
      setTouched(false)
    } else {
      setConvId(conversationId)
    }
  }, [open, conversationId])

  const active = useMemo(
    () => (requisites.data ?? []).filter((requisite) => requisite.is_active),
    [requisites.data],
  )

  useEffect(() => {
    if (requisiteId === null && active.length > 0) setRequisiteId(String(active[0].id))
  }, [active, requisiteId])

  const total = draftTotal(items)
  const validation = itemsError(items)
  const needsRequisite = method === 'requisites'
  const canSend = !validation && (!needsRequisite || requisiteId !== null) && !createAndSend.isPending

  function patch(index: number, field: 'name' | 'amount', value: string) {
    setItems((prev) =>
      prev.map((item, i) => (i === index ? { ...item, [field]: value } : item)),
    )
  }

  async function submit() {
    setTouched(true)
    if (validation) {
      setError(validation)
      return
    }
    if (needsRequisite && requisiteId === null) {
      setError('Выберите счёт получателя')
      return
    }
    setError(null)
    try {
      await createAndSend.mutateAsync({
        conversation_id: convId,
        payment_method: method,
        requisite_id: needsRequisite ? Number(requisiteId) : undefined,
        items: toItemsPayload(items),
        intro_text: introText.trim() || undefined,
      })
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось отправить счёт')
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Создать оплату"
      description={`Клиент: ${clientName}`}
      footer={
        <Button fullWidth loading={createAndSend.isPending} disabled={!canSend} onClick={() => void submit()}>
          {method === 'link' ? 'Отправить ссылку' : 'Отправить реквизиты'} · {money(total)}
        </Button>
      }
    >
      <div className="flex flex-col gap-5">
        {conversations && conversations.length > 1 && (
          <Field label="В какой чат отправить счёт">
            <Select
              value={String(convId)}
              onChange={(value) => setConvId(Number(value))}
              options={conversations.map((c) => ({ value: String(c.id), label: c.label }))}
            />
          </Field>
        )}

        <section className="flex flex-col gap-2">
          <span className="text-label uppercase tracking-wide text-ink-faint">Способ оплаты</span>
          <div className="grid grid-cols-2 gap-2">
            <button
              type="button"
              disabled={!me.robokassa_enabled}
              title={me.robokassa_enabled ? undefined : LINK_NOT_READY}
              onClick={() => setMethod('link')}
              className={
                !me.robokassa_enabled
                  ? 'flex cursor-not-allowed flex-col items-start gap-0.5 rounded-md border border-line bg-surface-raised/50 px-3 py-2.5 text-left opacity-60'
                  : `flex flex-col items-start gap-0.5 rounded-md border px-3 py-2.5 text-left transition-colors ${
                      method === 'link'
                        ? 'border-accent bg-accent-soft'
                        : 'border-line bg-surface-raised hover:border-line-strong'
                    }`
              }
            >
              <span className={method === 'link' ? 'text-sm text-accent-text' : 'text-sm text-ink-muted'}>
                Ссылка
              </span>
              <span className="text-micro text-ink-faint">
                {me.robokassa_enabled ? 'через Робокассу' : 'пока недоступно'}
              </span>
            </button>
            <button
              type="button"
              onClick={() => setMethod('requisites')}
              className={`flex flex-col items-start gap-0.5 rounded-md border px-3 py-2.5 text-left transition-colors ${
                method === 'requisites'
                  ? 'border-accent bg-accent-soft'
                  : 'border-line bg-surface-raised hover:border-line-strong'
              }`}
            >
              <span
                className={method === 'requisites' ? 'text-sm text-accent-text' : 'text-sm text-ink-muted'}
              >
                Реквизиты
              </span>
              <span className="text-micro text-ink-faint">перевод по счёту</span>
            </button>
          </div>
          {!me.robokassa_enabled && (
            <span className="flex items-start gap-1.5 text-micro text-ink-faint">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {LINK_NOT_READY}
            </span>
          )}
        </section>

        <section className="flex flex-col gap-2">
          <span className="text-label uppercase tracking-wide text-ink-faint">
            Услуги · {items.length}
          </span>
          {items.map((item, index) => (
            <div key={item.key} className="flex flex-col gap-2 rounded-md bg-surface-raised p-3">
              <div className="flex items-center gap-2">
                <Input
                  className="flex-1 bg-surface"
                  value={item.name}
                  placeholder="Название услуги"
                  onChange={(event) => patch(index, 'name', event.target.value)}
                />
                {items.length > 1 && (
                  <button
                    aria-label="Убрать услугу"
                    onClick={() => setItems((prev) => prev.filter((_, i) => i !== index))}
                    className="flex size-9 shrink-0 items-center justify-center rounded text-ink-faint transition-colors hover:bg-surface hover:text-danger"
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                )}
              </div>
              <Input
                className="bg-surface"
                inputMode="numeric"
                value={item.amount}
                placeholder="Стоимость, ₽"
                onChange={(event) => patch(index, 'amount', event.target.value)}
                trailing={
                  <span className="tnum text-micro text-ink-faint">
                    {parseMoney(item.amount) ? money(parseMoney(item.amount)!) : ''}
                  </span>
                }
              />
            </div>
          ))}
          <Button
            variant="secondary"
            size="sm"
            className="self-start"
            onClick={() => setItems((prev) => [...prev, newItemDraft()])}
          >
            <Plus className="size-4" aria-hidden />
            Добавить услугу
          </Button>
        </section>

        {needsRequisite && (
          <Field label="Счёт получателя" required>
            <Select
              value={requisiteId}
              onChange={setRequisiteId}
              placeholder={requisites.isLoading ? 'Загружаем…' : 'Выберите счёт'}
              // Счета сгруппированы по стране: отправить казахстанскую карту
              // клиенту из России — значит не получить оплату.
              options={active.map((requisite) => ({
                value: String(requisite.id),
                label: requisite.country
                  ? `${requisite.country} — ${requisite.method ?? requisite.title}`
                  : requisite.title,
                hint: [requisite.kind, requisite.account_masked, requisite.holder]
                  .filter(Boolean)
                  .join(' · '),
              }))}
            />
          </Field>
        )}

        {/* ТЗ п. 4.4 и 4.5: сопроводительный текст уходит клиенту перед
            реквизитами или ссылкой — одинаково для обоих способов оплаты. */}
        <Field
          label="Текст к счёту"
          hint={`Уйдёт клиенту первым сообщением, перед ${needsRequisite ? 'реквизитами' : 'ссылкой'}`}
        >
          <Textarea
            rows={3}
            value={introText}
            onChange={(event) => setIntroText(event.target.value)}
            placeholder={
              needsRequisite
                ? 'Вот ваша оплата ✨ Реквизиты ниже, в комментарии укажите код платежа.'
                : 'Вот ваша оплата ✨ Ссылка ниже.'
            }
          />
        </Field>

        <div className="flex items-baseline justify-between rounded-md bg-surface-raised px-3 py-3">
          <span className="text-sm text-ink-muted">Итого</span>
          <span className="tnum text-xl font-semibold text-ink">{money(total)}</span>
        </div>

        <p className="text-xs leading-relaxed text-ink-faint">
          {needsRequisite
            ? 'Клиенту уйдут реквизиты, сумма и код платежа — он укажет его в комментарии к переводу. Поступление подтверждается вручную в карточке оплаты.'
            : 'Клиенту уйдёт ссылка на оплату через Робокассу. Поступление подтвердится само, как только банк проведёт платёж.'}
        </p>

        {(error || (touched && validation)) && (
          <InlineError message={error ?? validation ?? ''} />
        )}
      </div>
    </Sheet>
  )
}
