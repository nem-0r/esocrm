import {
  Ban,
  Check,
  Copy,
  Download,
  FileText,
  Info,
  Paperclip,
  Pencil,
  Plus,
  Send,
  Trash2,
  X,
} from 'lucide-react'
import { useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '@/shared/api/client'
import { dateTimeFull, fileSize, money } from '@/shared/lib/format'
import {
  Avatar,
  BackButton,
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
  Select,
  Sheet,
  Textarea,
} from '@/shared/ui'
import { ImagePreview } from '@/features/chats/components/MediaAttachment'
import { PaymentsLayout } from '@/features/deals/components/PaymentsLayout'
import {
  DEAL_EVENT_LABEL,
  DEAL_STATUS_LABEL,
  DEAL_STATUS_TONE,
  PAYMENT_METHOD_LABEL,
  canCancel,
  canEdit,
  canPay,
  canSend,
  draftTotal,
  expiryLabel,
  itemsError,
  newItemDraft,
  toItemsPayload,
  type ItemDraft,
} from '@/features/deals/lib'
import {
  useCancelDeal,
  useDeal,
  usePayDeal,
  useRequisites,
  useSendDeal,
  useUpdateDeal,
} from '@/features/deals/queries'

export function DealCardPage() {
  const { dealId } = useParams<{ dealId: string }>()
  const id = Number(dealId)

  if (!Number.isFinite(id)) {
    return (
      <PaymentsLayout selectedId={null}>
        <EmptyState title="Оплата не найдена" />
      </PaymentsLayout>
    )
  }

  return (
    <PaymentsLayout selectedId={id}>
      <DealPane dealId={id} />
    </PaymentsLayout>
  )
}

interface ReceiptUpload {
  upload_key: string
  file_name: string
  size_bytes: number
  mime_type: string | null
}

function DealPane({ dealId }: { dealId: number }) {
  const card = useDeal(dealId)
  const conversationId = card.data?.conversation_id ?? null

  const send = useSendDeal(dealId, conversationId)
  const pay = usePayDeal(dealId, conversationId)
  const cancel = useCancelDeal(dealId, conversationId)
  const update = useUpdateDeal(dealId, conversationId)

  const [payOpen, setPayOpen] = useState(false)
  const [cancelOpen, setCancelOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [receipt, setReceipt] = useState<ReceiptUpload | null>(null)
  const [receiptUploading, setReceiptUploading] = useState(false)
  const [receiptError, setReceiptError] = useState<string | null>(null)
  const receiptInput = useRef<HTMLInputElement>(null)
  const [paidTo, setPaidTo] = useState<string | null>(null)
  const requisites = useRequisites()
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  async function onPickReceipt(files: FileList | null) {
    const file = files?.[0]
    if (!file) return
    setReceiptError(null)
    setReceiptUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      setReceipt(await api.upload<ReceiptUpload>('/files/upload', form))
    } catch (cause) {
      setReceiptError(cause instanceof ApiError ? cause.message : 'Не удалось загрузить чек')
    } finally {
      setReceiptUploading(false)
      if (receiptInput.current) receiptInput.current.value = ''
    }
  }

  const [editOpen, setEditOpen] = useState(false)
  const [editItems, setEditItems] = useState<ItemDraft[]>([newItemDraft()])
  const [editRequisiteId, setEditRequisiteId] = useState<string | null>(null)
  const [editComment, setEditComment] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [editTouched, setEditTouched] = useState(false)

  if (card.isLoading) return <ListSkeleton rows={5} />
  if (card.error) return <ErrorState error={card.error} onRetry={() => void card.refetch()} />
  if (!card.data) return <EmptyState title="Оплата не найдена" />

  const deal = card.data

  const editNeedsRequisite = deal.payment_method === 'requisites'
  const editActiveRequisites = (requisites.data ?? []).filter((r) => r.is_active)
  const editValidation = itemsError(editItems)
  const editTotal = draftTotal(editItems)
  const editCanSubmit =
    !editValidation &&
    (!editNeedsRequisite || editRequisiteId !== null) &&
    editComment.trim().length > 0 &&
    !update.isPending

  async function submitEdit() {
    setEditTouched(true)
    if (editValidation) {
      setEditError(editValidation)
      return
    }
    if (editNeedsRequisite && editRequisiteId === null) {
      setEditError('Выберите счёт получателя')
      return
    }
    if (!editComment.trim()) {
      setEditError('Укажите причину изменения')
      return
    }
    setEditError(null)
    try {
      await update.mutateAsync({
        items: toItemsPayload(editItems),
        requisite_id: editNeedsRequisite ? Number(editRequisiteId) : undefined,
        comment: editComment.trim(),
      })
      setEditOpen(false)
    } catch (cause) {
      setEditError(cause instanceof Error ? cause.message : 'Не удалось сохранить изменения')
    }
  }

  async function run(action: () => Promise<unknown>, onDone?: () => void) {
    setError(null)
    try {
      await action()
      onDone?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось выполнить действие')
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <BackButton fallback="/payments" className="desk:hidden" />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex items-center gap-2">
              <Badge tone={DEAL_STATUS_TONE[deal.status]}>{DEAL_STATUS_LABEL[deal.status]}</Badge>
              <span className="tnum text-xs text-ink-faint">{deal.number}</span>
              {deal.edit_count > 0 && <Badge tone="warning">изменена</Badge>}
            </div>
            <span className="tnum text-2xl font-semibold text-ink">{money(deal.total_amount)}</span>
          </div>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
          <Link to={`/clients/${deal.client.id}`} className="flex items-center gap-1.5">
            <Avatar name={deal.client.name} size="sm" />
            {deal.client.name}
          </Link>
          <span>· создана {dateTimeFull(deal.created_at)}</span>
          <span>· продал {deal.sold_by.full_name}</span>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex w-full max-w-2xl flex-col gap-4">
          {error && <InlineError message={error} />}

          <Card>
            <SectionTitle>Состав</SectionTitle>
            <ul className="flex flex-col gap-2">
              {deal.items.map((item) => (
                <li key={item.id} className="flex items-baseline justify-between gap-3">
                  <span className="text-sm text-ink">{item.name}</span>
                  <span className="tnum shrink-0 text-sm text-ink-muted">{money(item.amount)}</span>
                </li>
              ))}
            </ul>
            <div className="flex items-baseline justify-between border-t border-line pt-3">
              <span className="text-sm font-medium text-ink">Итого</span>
              <span className="tnum text-lg font-semibold text-ink">
                {money(deal.total_amount)}
              </span>
            </div>
          </Card>

          <Card>
            <SectionTitle>Оплата</SectionTitle>
            <p className="text-sm text-ink-muted">{PAYMENT_METHOD_LABEL[deal.payment_method]}</p>
            {deal.requisites_snapshot && (
              <pre className="whitespace-pre-wrap rounded-md bg-surface-raised p-3 text-xs leading-relaxed text-ink-muted">
                {deal.requisites_snapshot}
              </pre>
            )}
            {deal.payment_method === 'link' && deal.payment_url && (
              <div className="flex items-start gap-2 rounded-md bg-accent-soft px-3 py-2.5">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm font-medium text-accent-text">
                    {deal.payment_url}
                  </span>
                  <span className="text-micro text-ink-faint">
                    Ссылка на оплату через Робокассу — поступление подтвердится само
                  </span>
                </div>
                <button
                  aria-label="Скопировать ссылку на оплату"
                  onClick={() => {
                    void navigator.clipboard.writeText(deal.payment_url ?? '')
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1600)
                  }}
                  className="relative shrink-0 text-ink-faint transition-colors hover:text-ink before:absolute before:-inset-3.5 before:content-['']"
                >
                  {copied ? (
                    <Check className="size-4" aria-hidden />
                  ) : (
                    <Copy className="size-4" aria-hidden />
                  )}
                </button>
              </div>
            )}
            {expiryLabel(deal.expires_at) && (
              <p className="text-xs text-ink-faint">{expiryLabel(deal.expires_at)}</p>
            )}
            {deal.status === 'cancelled' && deal.cancel_reason && (
              <p className="text-xs text-danger">Причина отмены: {deal.cancel_reason}</p>
            )}
            {deal.status === 'paid' && (
              <p className="text-xs text-ink-faint">Оплаченную сделку изменить нельзя.</p>
            )}
            {deal.receipt_url && (
              <div className="flex flex-col gap-1.5">
                <span className="text-label uppercase tracking-wide text-ink-faint">
                  Чек оплаты
                </span>
                {deal.receipt_mime_type?.startsWith('image/') ? (
                  <ImagePreview src={deal.receipt_url} alt={deal.receipt_file_name ?? 'Чек'} />
                ) : (
                  <a
                    href={deal.receipt_url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-2 rounded-md bg-surface-raised px-3 py-2.5 transition-colors hover:bg-line"
                  >
                    <FileText className="size-4 shrink-0 text-accent-text" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm text-ink">
                        {deal.receipt_file_name}
                      </span>
                      {deal.receipt_size_bytes !== null && (
                        <span className="block text-micro text-ink-faint">
                          {fileSize(deal.receipt_size_bytes)}
                        </span>
                      )}
                    </span>
                    <Download className="size-4 shrink-0 text-ink-faint" aria-hidden />
                  </a>
                )}
              </div>
            )}
          </Card>

          <Card>
            <SectionTitle>Журнал</SectionTitle>
            <ul className="flex flex-col gap-3">
              {deal.events.map((event) => (
                <li key={event.id} className="flex flex-col gap-0.5">
                  <span className="text-sm text-ink">{DEAL_EVENT_LABEL[event.kind]}</span>
                  {event.comment && <span className="text-xs text-ink-muted">{event.comment}</span>}
                  <span className="text-micro text-ink-faint">
                    {event.actor ? `${event.actor.full_name} · ` : ''}
                    {dateTimeFull(event.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>

      {(canSend(deal.status) || canPay(deal.status) || canCancel(deal.status)) && (
        <div className="shrink-0 border-t border-line bg-surface px-4 pt-3 pb-safe-3">
          <div className="mx-auto flex w-full max-w-2xl flex-col gap-2 desk:flex-row">
            {canSend(deal.status) && (
              <Button
                fullWidth
                loading={send.isPending}
                onClick={() => void run(() => send.mutateAsync())}
              >
                <Send className="size-4" aria-hidden />
                Отправить в чат
              </Button>
            )}
            {canPay(deal.status) && (
              <Button
                fullWidth
                onClick={() => {
                  // Предзаполняем реквизитом из счёта: чаще всего деньги приходят
                  // именно туда, и менеджеру остаётся только приложить чек.
                  setPaidTo(deal.requisite_id ? String(deal.requisite_id) : null)
                  setPayOpen(true)
                }}
              >
                <Check className="size-4" aria-hidden />
                Отметить оплаченной
              </Button>
            )}
            {canEdit(deal.status) && (
              <Button
                fullWidth
                variant="secondary"
                onClick={() => {
                  setEditItems(
                    deal.items.length > 0
                      ? deal.items.map((item) => ({
                          key: `edit-${item.id}`,
                          name: item.name,
                          amount: (item.amount / 100).toFixed(2),
                        }))
                      : [newItemDraft()],
                  )
                  setEditRequisiteId(deal.requisite_id ? String(deal.requisite_id) : null)
                  setEditComment('')
                  setEditError(null)
                  setEditTouched(false)
                  setEditOpen(true)
                }}
                title="Изменение состава требует указать причину"
              >
                <Pencil className="size-4" aria-hidden />
                Изменить
              </Button>
            )}
            {canCancel(deal.status) && (
              <Button fullWidth variant="danger" onClick={() => setCancelOpen(true)}>
                <Ban className="size-4" aria-hidden />
                Отменить
              </Button>
            )}
          </div>
        </div>
      )}

      {/* ТЗ п. 6.3: подтверждение оплаты требует приложенный чек. Оплата без
          чека — выручка, которой нет в кассе: расхождение всплывёт при сверке. */}
      <Sheet
        open={payOpen}
        onOpenChange={setPayOpen}
        title="Подтвердить оплату"
        description={
          deal.payment_method === 'requisites'
            ? `Поступление на ${money(deal.total_amount)} — приложите чек, который прислал клиент`
            : `Поступление на ${money(deal.total_amount)} — подтверждаете вручную, в обход Робокассы`
        }
        className="desk:w-[460px]"
        footer={
          <Button
            fullWidth
            disabled={!receipt || receiptUploading}
            loading={pay.isPending}
            onClick={() =>
              void run(
                () =>
                  pay.mutateAsync({
                    receipt_upload_key: receipt!.upload_key,
                    receipt_file_name: receipt!.file_name,
                    receipt_mime_type: receipt!.mime_type,
                    receipt_size_bytes: receipt!.size_bytes,
                    paid_to_requisite_id: paidTo ? Number(paidTo) : null,
                  }),
                () => {
                  setPayOpen(false)
                  setReceipt(null)
                },
              )
            }
          >
            Да, оплачено
          </Button>
        }
      >
        <div className="flex flex-col gap-3">
          <Field label="Чек оплаты" hint="Без чека подтвердить нельзя" required>
            {receiptError && <InlineError message={receiptError} />}
            {receipt ? (
              <div className="flex items-center gap-2 rounded-md bg-surface-raised px-2.5 py-2">
                <FileText className="size-4 shrink-0 text-accent-text" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs text-ink">{receipt.file_name}</span>
                  <span className="block text-micro text-ink-faint">
                    {fileSize(receipt.size_bytes)}
                  </span>
                </span>
                <button
                  aria-label="Убрать чек"
                  onClick={() => setReceipt(null)}
                  className="text-ink-faint transition-colors hover:text-danger"
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              </div>
            ) : (
              <>
                <input
                  ref={receiptInput}
                  type="file"
                  accept="image/*,application/pdf"
                  className="hidden"
                  onChange={(event) => void onPickReceipt(event.target.files)}
                />
                <Button
                  variant="secondary"
                  fullWidth
                  loading={receiptUploading}
                  onClick={() => receiptInput.current?.click()}
                >
                  <Paperclip className="size-4" aria-hidden />
                  Прикрепить файл — фото или PDF
                </Button>
              </>
            )}
          </Field>
          {/* Куда деньги пришли фактически: клиент часто платит другим способом,
              и сверка с выпиской сойдётся только по реальному счёту. */}
          <Field label="Куда пришли деньги" hint="По умолчанию — реквизит из счёта">
            <Select
              value={paidTo}
              onChange={setPaidTo}
              options={(requisites.data ?? []).map((item) => ({
                value: String(item.id),
                label: item.country ? `${item.country} · ${item.title}` : item.title,
              }))}
            />
          </Field>
        </div>
      </Sheet>

      <Sheet
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Отменить сделку"
        description="Причина обязательна — она попадёт в журнал"
        className="desk:w-[460px]"
        footer={
          <Button
            variant="danger"
            fullWidth
            disabled={!reason.trim()}
            loading={cancel.isPending}
            onClick={() =>
              void run(
                () => cancel.mutateAsync({ reason: reason.trim() }),
                () => {
                  setCancelOpen(false)
                  setReason('')
                },
              )
            }
          >
            Отменить сделку
          </Button>
        }
      >
        <Field label="Причина отмены" required>
          <Textarea
            rows={4}
            value={reason}
            maxLength={500}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например: клиент передумал, вернёмся в следующем месяце"
          />
        </Field>
      </Sheet>

      {/* Реквизиты можно сменить только на действующие — как и при создании. */}
      <Sheet
        open={editOpen}
        onOpenChange={setEditOpen}
        title="Изменить сделку"
        description="Причина обязательна — попадёт в журнал"
        footer={
          <Button
            fullWidth
            loading={update.isPending}
            disabled={!editCanSubmit}
            onClick={() => void submitEdit()}
          >
            Сохранить · {money(editTotal)}
          </Button>
        }
      >
        <div className="flex flex-col gap-5">
          {deal.sent_at && (
            <p className="flex items-start gap-1.5 rounded-md bg-accent-soft px-3 py-2.5 text-xs text-accent-text">
              <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              Счёт уже отправлен клиенту. При изменении суммы или счёта получателя
              {deal.payment_method === 'link' ? ' пересоберём ссылку' : ' обновим реквизиты'} и
              пришлём клиенту новое сообщение — старые данные для оплаты перестанут действовать.
            </p>
          )}

          <section className="flex flex-col gap-2">
            <span className="text-label uppercase tracking-wide text-ink-faint">
              Услуги · {editItems.length}
            </span>
            {editItems.map((item, index) => (
              <div key={item.key} className="flex flex-col gap-2 rounded-md bg-surface-raised p-3">
                <div className="flex items-center gap-2">
                  <Input
                    className="flex-1 bg-surface"
                    value={item.name}
                    placeholder="Название услуги"
                    onChange={(event) =>
                      setEditItems((prev) =>
                        prev.map((row, i) =>
                          i === index ? { ...row, name: event.target.value } : row,
                        ),
                      )
                    }
                  />
                  {editItems.length > 1 && (
                    <button
                      aria-label="Убрать услугу"
                      onClick={() => setEditItems((prev) => prev.filter((_, i) => i !== index))}
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
                  onChange={(event) =>
                    setEditItems((prev) =>
                      prev.map((row, i) =>
                        i === index ? { ...row, amount: event.target.value } : row,
                      ),
                    )
                  }
                />
              </div>
            ))}
            <Button
              variant="secondary"
              size="sm"
              className="self-start"
              onClick={() => setEditItems((prev) => [...prev, newItemDraft()])}
            >
              <Plus className="size-4" aria-hidden />
              Добавить услугу
            </Button>
          </section>

          {editNeedsRequisite && (
            <Field label="Счёт получателя" required>
              <Select
                value={editRequisiteId}
                onChange={setEditRequisiteId}
                placeholder={requisites.isLoading ? 'Загружаем…' : 'Выберите счёт'}
                options={editActiveRequisites.map((requisite) => ({
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

          <Field label="Причина изменения" required>
            <Textarea
              rows={3}
              value={editComment}
              maxLength={500}
              onChange={(event) => setEditComment(event.target.value)}
              placeholder="Например: клиент попросил добавить услугу"
            />
          </Field>

          {(editError || (editTouched && editValidation)) && (
            <InlineError message={editError ?? editValidation ?? ''} />
          )}
        </div>
      </Sheet>
    </div>
  )
}
