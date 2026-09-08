import {
  AlertTriangle,
  History,
  Plus,
  RefreshCw,
  Smartphone,
  Trash2,
  UserPlus,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import type { Account } from '@/entities/types'
import { dateShort } from '@/shared/lib/format'
import {
  Avatar,
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
  Select,
  Sheet,
  StatTile,
  Switch,
} from '@/shared/ui'
import { useMe } from '@/shared/hooks/useAuth'
import { ProfileScreen } from '@/features/profile/components/ProfileScreen'
import {
  FUNNEL_LABEL,
  FUNNEL_OPTIONS,
  STATUS_LABEL,
  STATUS_TONE,
  errorMessage,
} from '@/features/profile/lib'
import {
  useDeleteAccount,
  syncAccountHistory,
  useAccounts,
  useConfirmCode,
  useCreateAccount,
  useQrPassword,
  useQrStart,
  useQrState,
  useSendCode,
  useSetAccountManagers,
  useStaff,
} from '@/features/profile/queries'

export function AccountsPage() {
  const [onlyAttention, setOnlyAttention] = useState(false)
  const [connectOpen, setConnectOpen] = useState(false)
  const [managersFor, setManagersFor] = useState<Account | null>(null)
  const [reconnect, setReconnect] = useState<Account | null>(null)
  const [syncing, setSyncing] = useState<number | null>(null)
  const [syncNote, setSyncNote] = useState<string | null>(null)
  const [removing, setRemoving] = useState<Account | null>(null)
  const removeAccount = useDeleteAccount()
  const [actionError, setActionError] = useState<string | null>(null)
  const accounts = useAccounts()
  const me = useMe()

  async function pullHistory(account: Account) {
    setActionError(null)
    setSyncNote(null)
    setSyncing(account.id)
    try {
      await syncAccountHistory(account.id)
      // Работа идёт на шлюзе и занимает минуты: честно говорим «началась»,
      // а не «готово». Новые чаты появятся в списке сами.
      setSyncNote(`Подтяжка переписки «${account.title}» началась. Чаты появятся по мере загрузки.`)
    } catch (cause) {
      setActionError(errorMessage(cause))
    } finally {
      setSyncing(null)
    }
  }

  const all = accounts.data ?? []
  const attentionCount = all.filter((a) => a.needs_attention).length
  const rows = all.filter((a) => !onlyAttention || a.needs_attention)

  return (
    <ProfileScreen
      title="Аккаунты"
      subtitle="Клиенты пишут на эти номера, менеджеры отвечают из CRM"
      backTo="/profile"
      action={
        <Button size="sm" onClick={() => setConnectOpen(true)}>
          <Plus className="size-4" aria-hidden />
          Подключить
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {/* ТЗ Б.9: сколько аккаунтов требуют внимания — видно сразу сверху,
            не пересчитывая список глазами. */}
        <div className="grid grid-cols-3 gap-2">
          <StatTile value={all.length} label="аккаунтов" />
          <StatTile
            value={all.filter((a) => a.status === 'connected').length}
            label="подключено"
            tone="success"
          />
          <StatTile
            value={attentionCount}
            label="требуют внимания"
            tone={attentionCount > 0 ? 'warning' : 'neutral'}
          />
        </div>

        <Switch
          checked={onlyAttention}
          onChange={setOnlyAttention}
          label="Только требующие внимания"
          hint="Нет менеджера или прервана сессия"
        />

        {accounts.isLoading ? (
          <ListSkeleton rows={4} />
        ) : accounts.error ? (
          <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title={onlyAttention ? 'Всё в порядке' : 'Аккаунтов пока нет'}
            hint={
              onlyAttention
                ? 'Ни один аккаунт не требует внимания.'
                : 'Подключите рабочий Telegram-аккаунт, чтобы принимать сообщения.'
            }
            icon={<Smartphone className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((account) => (
              <li key={account.id}>
                <Card>
                  <div className="flex items-start gap-3">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-ink">
                          {account.title}
                        </span>
                        <Badge tone={STATUS_TONE[account.status]}>
                          {STATUS_LABEL[account.status]}
                        </Badge>
                        {account.needs_attention && (
                          <Badge tone="warning">
                            <AlertTriangle className="size-3" aria-hidden />
                            требует внимания
                          </Badge>
                        )}
                      </div>
                      <span className="tnum text-xs text-ink-faint">
                        {account.phone} · {FUNNEL_LABEL[account.funnel_stage]} ·{' '}
                        {account.conversations_count} диалогов
                      </span>
                      {account.status_reason && (
                        <span className="text-xs text-danger">{account.status_reason}</span>
                      )}
                      {account.last_activity_at && (
                        <span className="text-micro text-ink-faint">
                          активность {dateShort(account.last_activity_at)}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 border-t border-line pt-3">
                    <div className="flex min-w-0 flex-1 items-center gap-1.5">
                      {account.managers.length === 0 ? (
                        <span className="text-xs text-warning">Менеджер не назначен</span>
                      ) : (
                        <>
                          {/* Подпись + ограничение ряда: без неё аватары рядом читаются
                              как «общая команда ведёт всё», хотя каждый видит только
                              свои диалоги (см. описание в ManagersSheet). */}
                          <span className="shrink-0 text-micro text-ink-faint">Назначены</span>
                          {account.managers.slice(0, 3).map((manager) => (
                            <Avatar
                              key={manager.id}
                              name={manager.full_name}
                              color={manager.avatar_color}
                              size="sm"
                              online={manager.online}
                            />
                          ))}
                          {account.managers.length > 3 && (
                            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-surface-raised text-micro font-semibold text-ink-muted">
                              +{account.managers.length - 3}
                            </span>
                          )}
                        </>
                      )}
                    </div>
                    {/* ТЗ Б.10: переподключить, если сессия слетела, и отключить
                        аккаунт — прямо здесь, не уходя в другой экран. */}
                    <div className="flex shrink-0 items-center gap-1.5">
                      <Button size="sm" variant="secondary" onClick={() => setManagersFor(account)}>
                        <UserPlus className="size-4" aria-hidden />
                        Кто работает
                      </Button>
                      {/* Подтяжка переписки: после подключения она идёт сама, но
                          иногда нужна ещё раз — например, аккаунт долго стоял
                          отключённым и пропустил часть истории. */}
                      <button
                        onClick={() => void pullHistory(account)}
                        disabled={
                          syncing === account.id ||
                          account.status !== 'connected' ||
                          me.demo_mode
                        }
                        title={
                          me.demo_mode
                            ? 'Демо-режим: переписка уже загружена демонстрационными данными'
                            : account.status === 'connected'
                              ? 'Подтянуть переписку из Telegram'
                              : 'Аккаунт не подключён'
                        }
                        aria-label="Подтянуть переписку из Telegram"
                        className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink disabled:opacity-40"
                      >
                        <History className="size-4" aria-hidden />
                      </button>
                      <button
                        onClick={() => setReconnect(account)}
                        title="Переподключить аккаунт"
                        aria-label="Переподключить аккаунт"
                        className="flex size-9 items-center justify-center rounded text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
                      >
                        <RefreshCw className="size-4" aria-hidden />
                      </button>
                      <button
                        onClick={() => setRemoving(account)}
                        title="Удалить аккаунт — без возможности восстановить"
                        aria-label="Удалить аккаунт — без возможности восстановить"
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

      <ConnectSheet open={connectOpen} onOpenChange={setConnectOpen} />
      <ConnectSheet
        open={reconnect !== null}
        onOpenChange={(open) => !open && setReconnect(null)}
        reconnecting={reconnect}
      />
      <ManagersSheet account={managersFor} onClose={() => setManagersFor(null)} />

      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title="Удалить аккаунт"
        message={
          removing
            ? `Аккаунт «${removing.title}» будет удалён без возможности восстановить или переподключить — этой записи и кнопки «Переподключить» больше не будет. Переписка и оплаты по нему останутся видны в CRM. Если номер понадобится снова — подключите его как новый аккаунт.`
            : ''
        }
        confirmLabel="Удалить безвозвратно"
        danger
        loading={removeAccount.isPending}
        onConfirm={async () => {
          if (!removing) return
          setActionError(null)
          try {
            await removeAccount.mutateAsync(removing.id)
            setRemoving(null)
          } catch (cause) {
            setActionError(errorMessage(cause))
          }
        }}
      />
      {actionError && <InlineError message={actionError} />}
      {syncNote && <p className="px-1 text-xs text-ink-muted">{syncNote}</p>}
    </ProfileScreen>
  )
}

function ManagersSheet({ account, onClose }: { account: Account | null; onClose: () => void }) {
  const staff = useStaff({})
  const save = useSetAccountManagers()
  const [selected, setSelected] = useState<number[]>([])
  const [notify, setNotify] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (account) setSelected(account.managers.map((m) => m.id))
  }, [account])

  // Кандидаты на назначение — только активные менеджеры, но если кого-то уже
  // назначили, а потом отключили — он должен остаться в списке, иначе его
  // никак не снять с аккаунта через этот экран (см. связанный finding).
  const assignedIds = new Set((account?.managers ?? []).map((m) => m.id))
  const candidates = (staff.data ?? []).filter(
    (s) => s.role === 'manager' && (s.is_active || assignedIds.has(s.id)),
  )

  return (
    <Sheet
      open={account !== null}
      onOpenChange={(open) => !open && onClose()}
      title="Кто работает на аккаунте"
      description="Каждый видит и ведёт свои диалоги на этом аккаунте, а ничейные может забрать из очереди. Диалоги, закреплённые за другим менеджером, ему не видны."
      footer={
        <Button
          fullWidth
          loading={save.isPending}
          onClick={async () => {
            if (!account) return
            setError(null)
            try {
              await save.mutateAsync({ accountId: account.id, userIds: selected, notify })
              onClose()
            } catch (cause) {
              setError(errorMessage(cause))
            }
          }}
        >
          Сохранить · выбрано {selected.length}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        {staff.isLoading ? (
          <ListSkeleton rows={3} />
        ) : (
          <ul className="flex flex-col gap-1.5">
            {candidates.map((member) => {
              const checked = selected.includes(member.id)
              return (
                <li key={member.id}>
                  <button
                    onClick={() =>
                      setSelected((prev) =>
                        checked ? prev.filter((id) => id !== member.id) : [...prev, member.id],
                      )
                    }
                    className={`flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors ${
                      checked ? 'bg-accent-soft' : 'bg-surface-raised hover:bg-line'
                    }`}
                  >
                    <span
                      className={`flex size-5 shrink-0 items-center justify-center rounded border ${
                        checked ? 'border-accent bg-accent text-white' : 'border-line-strong'
                      }`}
                      aria-hidden
                    >
                      {checked ? '✓' : ''}
                    </span>
                    <Avatar
                      name={member.full_name}
                      color={member.avatar_color}
                      size="sm"
                      online={member.online}
                    />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="flex items-center gap-1.5 truncate text-sm text-ink">
                        {member.full_name}
                        {!member.is_active && <Badge tone="danger">отключён</Badge>}
                      </span>
                      <span className="text-micro text-ink-faint">
                        {member.accounts.length} аккаунтов
                      </span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}

        <Switch
          checked={notify}
          onChange={setNotify}
          label="Уведомить об изменениях"
          hint="Уведомление получат все назначенные и снятые менеджеры"
        />

        <p className="text-xs text-ink-faint">
          Продажа засчитывается тому, кто создал оплату. Снятие менеджера не меняет прошлую
          статистику.
        </p>

        {error && <InlineError message={error} />}
      </div>
    </Sheet>
  )
}

function ConnectSheet({
  open,
  onOpenChange,
  reconnecting,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** ТЗ Б.10: если передан аккаунт — не заводим новый, а входим заново
      в существующий: сессия слетела, номер и название прежние. */
  reconnecting?: Account | null
}) {
  const create = useCreateAccount()
  const sendCode = useSendCode()
  const confirmCode = useConfirmCode()
  const qrStart = useQrStart()
  const qrPassword = useQrPassword()
  const queryClient = useQueryClient()

  const [step, setStep] = useState<'form' | 'qr' | 'code' | 'password'>('form')
  const [accountId, setAccountId] = useState<number | null>(null)
  const [title, setTitle] = useState('')
  const [phone, setPhone] = useState('+7')
  const [stage, setStage] = useState<string | null>('sales')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [hint, setHint] = useState<string | null>(null)
  const [codeHash, setCodeHash] = useState('')
  const [error, setError] = useState<string | null>(null)
  /** Каким путём идёт вход: от этого зависит, куда отправлять облачный пароль. */
  const [via, setVia] = useState<'qr' | 'code'>('qr')
  const [qrImage, setQrImage] = useState<string | null>(null)

  // Опрашиваем шлюз, только пока код на экране: пока идёт ожидание, шлюз
  // держит открытое соединение с Telegram.
  const qrState = useQrState(accountId, open && step === 'qr')

  useEffect(() => {
    if (!open) {
      setStep('form')
      setAccountId(null)
      setTitle('')
      setPhone('+7')
      setCode('')
      setPassword('')
      setHint(null)
      setCodeHash('')
      setError(null)
      setVia('qr')
      setQrImage(null)
      return
    }
    // Переподключение: аккаунт уже есть, заводить второй нельзя — входим
    // заново в него же.
    if (reconnecting) {
      setTitle(reconnecting.title)
      setPhone(reconnecting.phone)
      setStage(reconnecting.funnel_stage)
      setAccountId(reconnecting.id)
    }
  }, [open, reconnecting])

  // Шлюз сам обновляет протухший токен, поэтому картинку берём из опроса, а не
  // из ответа на запуск: иначе на экране осталась бы уже недействительная.
  const polled = qrState.data
  useEffect(() => {
    if (step !== 'qr' || !polled) return
    if (polled.image) setQrImage(polled.image)
    if (polled.status === 'password') {
      setVia('qr')
      setStep('password')
      return
    }
    if (polled.status === 'done') {
      // Список аккаунтов сам о входе не узнает: сессию записал шлюз, а не
      // мутация из этого окна.
      void queryClient.invalidateQueries({ queryKey: ['accounts'] })
      onOpenChange(false)
      return
    }
    if (polled.status === 'error') {
      setError(polled.message ?? 'Вход по QR не удался')
    }
  }, [polled, step, onOpenChange, queryClient])

  async function requestCodeFor(id: number) {
    const result = await sendCode.mutateAsync(id)
    setHint(result.hint ?? null)
    setCodeHash(result.phone_code_hash ?? '')
    setVia('code')
    setStep('code')
  }

  async function showQrFor(id: number) {
    const result = await qrStart.mutateAsync(id)
    setQrImage(result.image)
    setVia('qr')
    setStep('qr')
  }

  /** Завести аккаунт (если он ещё не заведён) и начать выбранный способ входа. */
  async function submitForm(mode: 'qr' | 'code') {
    setError(null)
    try {
      const id =
        reconnecting?.id ??
        accountId ??
        (
          await create.mutateAsync({
            title: title.trim(),
            phone: phone.trim(),
            funnel_stage: (stage ?? 'sales') as never,
          })
        ).id
      setAccountId(id)
      await (mode === 'qr' ? showQrFor(id) : requestCodeFor(id))
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  /** Показать новый код, когда прежний истёк или вход сорвался. */
  async function restartQr() {
    if (accountId === null) return
    setError(null)
    try {
      await showQrFor(accountId)
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  async function resendCode() {
    // Аккаунт уже создан на первом шаге — повторный запрос идёт по его id
    // и второй аккаунт не заводит.
    if (accountId === null) return
    setError(null)
    try {
      const result = await sendCode.mutateAsync(accountId)
      setHint(result.hint ?? null)
      setCodeHash(result.phone_code_hash ?? '')
      setCode('')
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  async function submitCode() {
    if (accountId === null) return
    setError(null)
    try {
      const result = await confirmCode.mutateAsync({
        accountId,
        code: code.trim(),
        phoneCodeHash: codeHash,
      })
      if (result.needs_password) {
        setStep('password')
        return
      }
      onOpenChange(false)
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  async function submitPassword() {
    if (accountId === null) return
    setError(null)
    try {
      if (via === 'qr') {
        await qrPassword.mutateAsync({ accountId, password })
      } else {
        await confirmCode.mutateAsync({
          accountId,
          code: code.trim(),
          phoneCodeHash: codeHash,
          password,
        })
      }
      onOpenChange(false)
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={reconnecting ? "Переподключить аккаунт" : "Подключить аккаунт"}
      description={
        step === 'form'
          ? 'Клиенты будут писать на этот номер, а менеджер отвечать из CRM'
          : step === 'qr'
            ? 'Отсканируйте код тем телефоном, на котором работает этот номер'
            : step === 'code'
              ? 'Код придёт в приложение Telegram, а не по СМС'
              : 'На аккаунте включён облачный пароль'
      }
      footer={
        step === 'form' ? (
          <div className="flex flex-col gap-2">
            <Button
              fullWidth
              loading={create.isPending || qrStart.isPending}
              disabled={!title.trim() || phone.trim().length < 11}
              onClick={() => void submitForm('qr')}
            >
              {reconnecting ? 'Войти заново по QR' : 'Показать QR-код'}
            </Button>
            <button
              onClick={() => void submitForm('code')}
              disabled={
                create.isPending ||
                sendCode.isPending ||
                !title.trim() ||
                phone.trim().length < 11
              }
              className="self-center text-xs text-muted underline-offset-4 transition-colors hover:text-text hover:underline disabled:opacity-45"
            >
              {sendCode.isPending ? 'Запрашиваем код…' : 'Войти по коду из Telegram'}
            </button>
          </div>
        ) : step === 'qr' ? (
          <Button fullWidth variant="secondary" onClick={() => void restartQr()} loading={qrStart.isPending}>
            Показать новый код
          </Button>
        ) : step === 'code' ? (
          <Button
            fullWidth
            loading={confirmCode.isPending}
            disabled={code.trim().length < 4}
            onClick={() => void submitCode()}
          >
            Подтвердить
          </Button>
        ) : (
          <Button
            fullWidth
            loading={confirmCode.isPending || qrPassword.isPending}
            disabled={!password}
            onClick={() => void submitPassword()}
          >
            Подключить аккаунт
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-4">
        {step === 'form' && (
          <>
            <Field label="Название для CRM" hint="Видят только сотрудники" required>
              <Input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Марина · продажи"
              />
            </Field>
            <Field label="Номер телефона" required>
              <Input value={phone} onChange={(event) => setPhone(event.target.value)} />
            </Field>
            <Field label="Этап воронки" hint="По нему видно, где сейчас клиент">
              <Select value={stage} onChange={setStage} options={FUNNEL_OPTIONS} />
            </Field>
            <p className="flex items-start gap-1.5 rounded-md bg-warning-soft px-3 py-2.5 text-xs text-warning">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              Телефон с этим номером должен быть под рукой: с него сканируют QR-код. Он же
              понадобится при восстановлении сессии.
            </p>
          </>
        )}

        {step === 'qr' && (
          <>
            {/* Белая подложка обязательна: на тёмном фоне код не считывается,
                камера ищет тёмные модули на светлом. */}
            <div className="flex justify-center rounded-xl bg-white p-4">
              {qrImage ? (
                <img
                  src={qrImage}
                  alt="QR-код для входа в Telegram"
                  className="size-56 max-w-full"
                />
              ) : (
                <div className="size-56 animate-pulse rounded-lg bg-black/10" />
              )}
            </div>
            <ol className="flex flex-col gap-1.5 text-xs text-muted">
              <li>1. Откройте Telegram на телефоне с этим номером</li>
              <li>2. Настройки → Устройства → Подключить устройство</li>
              <li>3. Наведите камеру на этот код</li>
            </ol>
            <p className="rounded-md bg-accent-soft px-3 py-2.5 text-xs text-accent-text">
              Код обновляется сам каждые полминуты — это нормально, сканируйте тот,
              что виден сейчас.
            </p>
            <button
              onClick={() => void submitForm('code')}
              disabled={sendCode.isPending}
              className="self-start text-xs text-muted underline-offset-4 transition-colors hover:text-text hover:underline disabled:opacity-45"
            >
              {sendCode.isPending ? 'Запрашиваем код…' : 'Войти по коду вместо QR'}
            </button>
          </>
        )}

        {step === 'code' && (
          <>
            <Field label="Код из Telegram" required>
              <Input
                value={code}
                inputMode="numeric"
                maxLength={6}
                onChange={(event) => setCode(event.target.value)}
                placeholder="38151"
                autoFocus
              />
            </Field>
            {hint && (
              <p className="rounded-md bg-accent-soft px-3 py-2.5 text-xs text-accent-text">
                {hint}
              </p>
            )}
            <button
              onClick={() => void resendCode()}
              disabled={sendCode.isPending}
              className="self-start text-xs text-accent-text underline-offset-4 transition-colors hover:underline disabled:opacity-45"
            >
              {sendCode.isPending ? 'Запрашиваем…' : 'Отправить код заново'}
            </button>
          </>
        )}

        {step === 'password' && (
          <Field label="Облачный пароль" required>
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoFocus
            />
          </Field>
        )}

        {error && <InlineError message={error} />}
      </div>
    </Sheet>
  )
}
