import { Landmark, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { Requisite } from '@/entities/types'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  InlineError,
  Input,
  ListSkeleton,
  Sheet,
  Switch,
  Textarea,
} from '@/shared/ui'
import { ProfileScreen } from '@/features/profile/components/ProfileScreen'
import { errorMessage } from '@/features/profile/lib'
import {
  useAdminRequisites,
  useCreateRequisite,
  useDeleteRequisite,
  useUpdateRequisite,
  type RequisiteInput,
} from '@/features/profile/queries'

export function RequisitesPage() {
  const requisites = useAdminRequisites()
  const [editing, setEditing] = useState<Requisite | 'new' | null>(null)
  const [removing, setRemoving] = useState<Requisite | null>(null)
  const deleteRequisite = useDeleteRequisite()
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const rows = requisites.data ?? []

  return (
    <ProfileScreen
      title="Реквизиты"
      subtitle="Счета, которые менеджер предлагает клиенту при оплате по реквизитам"
      backTo="/profile"
      action={
        <Button size="sm" onClick={() => setEditing('new')}>
          <Plus className="size-4" aria-hidden />
          Добавить
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        {requisites.isLoading ? (
          <ListSkeleton rows={3} />
        ) : requisites.error ? (
          <ErrorState error={requisites.error} onRetry={() => void requisites.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Реквизитов пока нет"
            hint="Добавьте хотя бы один счёт, чтобы принимать оплаты по реквизитам."
            icon={<Landmark className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((requisite) => (
              <li key={requisite.id}>
                <Card>
                  <div className="flex items-start gap-3">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-ink">
                          {requisite.title}
                        </span>
                        <Badge tone={requisite.is_active ? 'success' : 'neutral'}>
                          {requisite.is_active ? 'активен' : 'отключён'}
                        </Badge>
                      </div>
                      {(requisite.bank_name || requisite.account_masked) && (
                        <span className="text-xs text-ink-faint">
                          {[requisite.bank_name, requisite.account_masked]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                      )}
                      <span className="whitespace-pre-wrap text-xs text-ink-muted">
                        {requisite.details_text}
                      </span>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <button
                        onClick={() => setEditing(requisite)}
                        title="Изменить"
                        aria-label="Изменить"
                        className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
                      >
                        <Pencil className="size-4" aria-hidden />
                      </button>
                      <button
                        onClick={() => {
                          setDeleteError(null)
                          setRemoving(requisite)
                        }}
                        title="Удалить счёт"
                        aria-label="Удалить счёт"
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
      </div>

      <RequisiteSheet
        open={editing !== null}
        requisite={editing === 'new' ? null : editing}
        onClose={() => setEditing(null)}
      />

      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title="Удалить счёт"
        message={
          removing
            ? `Счёт «${removing.title}» больше не будет предложен менеджерам при создании оплаты. Уже отправленные клиентам счета с ним не изменятся.`
            : ''
        }
        confirmLabel="Удалить"
        danger
        loading={deleteRequisite.isPending}
        onConfirm={async () => {
          if (!removing) return
          try {
            await deleteRequisite.mutateAsync(removing.id)
            setRemoving(null)
          } catch (cause) {
            setDeleteError(errorMessage(cause))
          }
        }}
      />
      {deleteError && <InlineError message={deleteError} />}
    </ProfileScreen>
  )
}

function RequisiteSheet({
  open,
  requisite,
  onClose,
}: {
  open: boolean
  requisite: Requisite | null
  onClose: () => void
}) {
  const isNew = requisite === null
  const create = useCreateRequisite()
  const update = useUpdateRequisite(requisite?.id ?? 0)
  const pending = create.isPending || update.isPending

  const [title, setTitle] = useState('')
  const [bankName, setBankName] = useState('')
  const [accountMasked, setAccountMasked] = useState('')
  const [detailsText, setDetailsText] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Каждое открытие — заново из актуальных данных строки (или пустая форма
  // для нового счёта). Без сброса поля хранили бы правки предыдущего счёта.
  const key = requisite?.id ?? 'new'
  const [loadedKey, setLoadedKey] = useState(key)
  if (open && key !== loadedKey) {
    setLoadedKey(key)
    setTitle(requisite?.title ?? '')
    setBankName(requisite?.bank_name ?? '')
    setAccountMasked(requisite?.account_masked ?? '')
    setDetailsText(requisite?.details_text ?? '')
    setIsActive(requisite?.is_active ?? true)
    setError(null)
  }

  const canSubmit = title.trim().length > 0 && detailsText.trim().length > 0

  async function submit() {
    setError(null)
    const input: RequisiteInput = {
      title: title.trim(),
      bank_name: bankName.trim() || null,
      account_masked: accountMasked.trim() || null,
      details_text: detailsText.trim(),
      is_active: isActive,
    }
    try {
      if (isNew) {
        await create.mutateAsync(input)
      } else {
        await update.mutateAsync(input)
      }
      onClose()
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={isNew ? 'Добавить счёт' : 'Изменить счёт'}
      description="Текст «Куда переводить» уходит клиенту в чат дословно — пишите так, как должен увидеть клиент."
      footer={
        <Button fullWidth loading={pending} disabled={!canSubmit} onClick={() => void submit()}>
          {isNew ? 'Добавить' : 'Сохранить'}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Название для CRM" hint="Видят только сотрудники" required>
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="ИП Астахова Е. · расчётный счёт"
          />
        </Field>
        <Field label="Банк" hint="Необязательно">
          <Input value={bankName} onChange={(event) => setBankName(event.target.value)} />
        </Field>
        <Field label="Номер счёта или карты" hint="Показывается в списке маской">
          <Input
            value={accountMasked}
            onChange={(event) => setAccountMasked(event.target.value)}
            placeholder="•• 4417"
          />
        </Field>
        <Field label="Куда переводить" hint="Полный текст, который клиент увидит в чате" required>
          <Textarea
            rows={5}
            value={detailsText}
            onChange={(event) => setDetailsText(event.target.value)}
            placeholder={'Получатель: ...\nБанк: ...\nСчёт: ...'}
          />
        </Field>
        <Switch
          checked={isActive}
          onChange={setIsActive}
          label="Активен"
          hint="Отключённый счёт не предлагается при создании новой оплаты"
        />
        {error && <InlineError message={error} />}
      </div>
    </Sheet>
  )
}
