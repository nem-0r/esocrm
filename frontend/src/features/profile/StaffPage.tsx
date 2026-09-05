import { Mail, Search, UserPlus, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import type { StaffMember } from '@/entities/types'
import { money } from '@/shared/lib/format'
import { useMe } from '@/shared/hooks/useAuth'
import {
  Avatar,
  Badge,
  Button,
  Card,
  Dot,
  EmptyState,
  ErrorState,
  Field,
  InlineError,
  Input,
  ListSkeleton,
  Segmented,
  Select,
  Sheet,
  StatTile,
  Switch,
} from '@/shared/ui'
import { ProfileScreen } from '@/features/profile/components/ProfileScreen'
import { InviteLink } from '@/features/profile/components/InviteLink'
import {
  EMPTY_SCHEDULE,
  ROLE_LABEL,
  ROLE_OPTIONS,
  WEEK_DAYS,
  errorMessage,
  presenceLabel,
  type WorkScheduleDraft,
} from '@/features/profile/lib'
import {
  useAccounts,
  useInviteStaff,
  useResendInvite,
  useStaff,
  useUpdateStaff,
  type StaffStatusFilter,
} from '@/features/profile/queries'

type Filter = 'all' | StaffStatusFilter

export function StaffPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [inviteOpen, setInviteOpen] = useState(false)
  const [editing, setEditing] = useState<StaffMember | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), 300)
    return () => clearTimeout(timer)
  }, [search])

  const staff = useStaff({
    q: debounced || undefined,
    status: filter === 'all' ? undefined : filter,
  })
  const rows = staff.data ?? []

  // Переход из результатов поиска (?user=id) — открывает карточку конкретного
  // сотрудника, а не просто список. Параметр убираем сразу после открытия,
  // чтобы не переоткрывать её при обновлении списка или после закрытия.
  useEffect(() => {
    const userId = searchParams.get('user')
    if (!userId) return
    const target = rows.find((member) => member.id === Number(userId))
    if (!target) return
    setEditing(target)
    setSearchParams(
      (prev) => {
        prev.delete('user')
        return prev
      },
      { replace: true },
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, rows])

  return (
    <ProfileScreen
      title="Сотрудники"
      subtitle="Доступ, роли и назначение на аккаунты"
      backTo="/profile"
      action={
        <Button size="sm" onClick={() => setInviteOpen(true)}>
          <UserPlus className="size-4" aria-hidden />
          Пригласить
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Поиск по имени или почте"
          leading={<Search className="size-4" aria-hidden />}
        />

        <Segmented
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'Все' },
            { value: 'online', label: 'В сети' },
            { value: 'offline', label: 'Не в сети' },
            { value: 'no_account', label: 'Без аккаунта' },
          ]}
        />

        {staff.isLoading ? (
          <ListSkeleton rows={4} />
        ) : staff.error ? (
          <ErrorState error={staff.error} onRetry={() => void staff.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Никого не найдено"
            hint="Измените фильтр или пригласите нового сотрудника."
            icon={<Users className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((member) => (
              <li key={member.id}>
                <button
                  onClick={() => setEditing(member)}
                  className="w-full rounded-lg bg-surface p-4 text-left transition-colors hover:bg-surface-raised"
                >
                  <div className="flex items-start gap-3">
                    <Avatar
                      name={member.full_name}
                      color={member.avatar_color}
                      online={member.online}
                    />
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-semibold text-ink">
                          {member.full_name}
                        </span>
                        <Badge>{ROLE_LABEL[member.role]}</Badge>
                        {member.invite_pending && (
                          <Badge tone="warning">не принял приглашение</Badge>
                        )}
                        {!member.is_active && <Badge tone="danger">отключён</Badge>}
                      </div>
                      <span className="truncate text-xs text-ink-muted">{member.email}</span>
                      {/* ТЗ Б.12: перечисление аккаунтов убрано — оно занимало
                          всю строку и обрезалось. Аккаунты видны в карточке,
                          которая открывается по нажатию.
                          ТЗ Б.7: онлайн виден точкой, а не только текстом. */}
                      <span className="flex items-center gap-1.5 text-micro text-ink-faint">
                        <Dot tone={member.online ? 'success' : 'neutral'} />
                        {presenceLabel(member.online, member.last_seen_at)}
                        {member.accounts.length === 0 && ' · без аккаунта'}
                      </span>
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-0.5">
                      <span className="tnum text-sm font-medium text-ink">
                        {money(member.month_sales_amount)}
                      </span>
                      <span className="text-micro text-ink-faint">
                        {member.month_conversations} диалогов
                      </span>
                    </div>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <InviteSheet open={inviteOpen} onOpenChange={setInviteOpen} />
      <MemberSheet member={editing} onClose={() => setEditing(null)} />
    </ProfileScreen>
  )
}

function InviteSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const invite = useInviteStaff()
  const accounts = useAccounts()
  const [form, setForm] = useState({ full_name: '', email: '', phone: '' })
  const [role, setRole] = useState<string | null>('manager')
  const [accountIds, setAccountIds] = useState<number[]>([])
  const [inviteUrl, setInviteUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      setForm({ full_name: '', email: '', phone: '' })
      setAccountIds([])
      setInviteUrl(null)
      setError(null)
    }
  }, [open])

  async function submit() {
    setError(null)
    try {
      const created = await invite.mutateAsync({
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        phone: form.phone.trim() || undefined,
        role: (role ?? 'manager') as never,
        account_ids: accountIds,
      })
      setInviteUrl(created.invite_url)
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Пригласить сотрудника"
      description="Он задаст пароль сам по ссылке"
      footer={
        inviteUrl ? (
          <Button fullWidth variant="secondary" onClick={() => onOpenChange(false)}>
            Готово
          </Button>
        ) : (
          <Button
            fullWidth
            loading={invite.isPending}
            disabled={!form.full_name.trim() || !form.email.trim()}
            onClick={() => void submit()}
          >
            Отправить приглашение
          </Button>
        )
      }
    >
      {inviteUrl ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink">
            Сотрудник создан. Передайте ему ссылку — по ней он задаст пароль и войдёт.
          </p>
          <InviteLink url={inviteUrl} />
          <p className="flex items-start gap-1.5 text-xs text-ink-faint">
            <Mail className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            Письма пока не отправляются — передайте ссылку сотруднику сами.
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <Field label="Имя и фамилия" required>
            <Input
              value={form.full_name}
              onChange={(event) => setForm((p) => ({ ...p, full_name: event.target.value }))}
            />
          </Field>
          <Field label="Рабочая почта" required>
            <Input
              type="email"
              value={form.email}
              onChange={(event) => setForm((p) => ({ ...p, email: event.target.value }))}
            />
          </Field>
          <Field label="Телефон" hint="Необязательно">
            <Input
              value={form.phone}
              onChange={(event) => setForm((p) => ({ ...p, phone: event.target.value }))}
            />
          </Field>
          <Field label="Роль">
            <Select value={role} onChange={setRole} options={ROLE_OPTIONS} />
          </Field>
          <Field label="Аккаунты" hint="Можно назначить позже" group>
            <div className="flex flex-wrap gap-1.5">
              {(accounts.data ?? []).map((account) => {
                const checked = accountIds.includes(account.id)
                return (
                  <button
                    key={account.id}
                    onClick={() =>
                      setAccountIds((prev) =>
                        checked ? prev.filter((id) => id !== account.id) : [...prev, account.id],
                      )
                    }
                    className={`rounded px-2.5 py-1.5 text-xs transition-colors ${
                      checked ? 'bg-accent-soft text-accent-text' : 'bg-surface-raised text-ink-muted'
                    }`}
                  >
                    {account.title}
                  </button>
                )
              })}
            </div>
          </Field>
          {error && <InlineError message={error} />}
        </div>
      )}
    </Sheet>
  )
}

function MemberSheet({ member, onClose }: { member: StaffMember | null; onClose: () => void }) {
  const me = useMe()
  const update = useUpdateStaff()
  const resend = useResendInvite()
  const accounts = useAccounts()
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [role, setRole] = useState<string | null>(null)
  const [accountIds, setAccountIds] = useState<number[]>([])
  const [active, setActive] = useState(true)
  const [schedule, setSchedule] = useState<WorkScheduleDraft>(EMPTY_SCHEDULE)
  const [inviteUrl, setInviteUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (member) {
      setFullName(member.full_name)
      setEmail(member.email)
      setPhone(member.phone ?? '')
      setRole(member.role)
      setAccountIds(member.accounts.map((a) => a.id))
      setActive(member.is_active)
      // У сотрудника без графика дни пустые. Подставляем рабочую неделю:
      // иначе руководитель включает переключатель, видит ни одного выбранного
      // дня и получает отказ при сохранении вместо готовой к правке смены.
      setSchedule({
        enabled: member.schedule.enabled,
        days: member.schedule.days.length > 0 ? member.schedule.days : EMPTY_SCHEDULE.days,
        start: member.schedule.start ?? EMPTY_SCHEDULE.start,
        end: member.schedule.end ?? EMPTY_SCHEDULE.end,
      })
      setInviteUrl(null)
      setError(null)
    }
  }, [member])

  const isSelf = member?.id === me.id

  async function save() {
    if (!member) return
    setError(null)
    try {
      await update.mutateAsync({
        userId: member.id,
        patch: {
          full_name: fullName.trim(),
          email: email.trim(),
          phone: phone.trim() || null,
          role: (role ?? member.role) as never,
          is_active: active,
          account_ids: accountIds,
          schedule: {
            enabled: schedule.enabled,
            days: schedule.days,
            start: schedule.start,
            end: schedule.end,
          },
        },
      })
      onClose()
    } catch (cause) {
      setError(errorMessage(cause))
    }
  }

  return (
    <Sheet
      open={member !== null}
      onOpenChange={(open) => !open && onClose()}
      title={member?.full_name ?? ''}
      description={member?.email}
      footer={
        <Button
          fullWidth
          loading={update.isPending}
          disabled={!fullName.trim() || !email.trim()}
          onClick={() => void save()}
        >
          Сохранить
        </Button>
      }
    >
      {member && (
        <div className="flex flex-col gap-4">
          {/* ТЗ Б.12: по нажатию открывается карточка сотрудника, а не сразу
              форма. Сверху — кто он и как работает, ниже — управление доступом. */}
          <div className="flex items-center gap-3 rounded-lg bg-surface-raised px-3 py-2.5">
            <Avatar name={member.full_name} color={member.avatar_color} size="lg" />
            <div className="flex min-w-0 flex-col gap-0.5">
              <span className="flex items-center gap-1.5 text-xs text-ink-muted">
                <Dot tone={member.online ? 'success' : 'neutral'} />
                {presenceLabel(member.online, member.last_seen_at)}
              </span>
              <span className="text-micro text-ink-faint">
                {member.accounts.length > 0
                  ? member.accounts.map((a) => a.title).join(', ')
                  : 'аккаунты не назначены'}
              </span>
              {member.schedule.enabled && (
                <span className="text-micro text-ink-faint">
                  {member.schedule.summary} ·{' '}
                  {member.schedule.on_shift ? 'на смене' : 'вне смены'}
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <StatTile value={member.month_conversations} label="диалогов за месяц" />
            <StatTile
              value={money(member.month_sales_amount)}
              label="продаж за месяц"
              tone="success"
            />
          </div>

          <Field label="Имя и фамилия" required>
            <Input value={fullName} onChange={(event) => setFullName(event.target.value)} />
          </Field>

          <Field
            label="Почта"
            hint={isSelf ? 'Это логин для входа — после смены входить новой почтой' : 'Логин для входа сотрудника'}
            required
          >
            <Input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </Field>

          <Field label="Телефон" hint="Необязательно">
            <Input value={phone} onChange={(event) => setPhone(event.target.value)} />
          </Field>

          <Field label="Роль">
            <Select value={role} onChange={setRole} options={ROLE_OPTIONS} disabled={isSelf} />
          </Field>

          <Field label="Аккаунты" group>
            <div className="flex flex-wrap gap-1.5">
              {(accounts.data ?? []).map((account) => {
                const checked = accountIds.includes(account.id)
                return (
                  <button
                    key={account.id}
                    onClick={() =>
                      setAccountIds((prev) =>
                        checked ? prev.filter((id) => id !== account.id) : [...prev, account.id],
                      )
                    }
                    className={`rounded px-2.5 py-1.5 text-xs transition-colors ${
                      checked ? 'bg-accent-soft text-accent-text' : 'bg-surface-raised text-ink-muted'
                    }`}
                  >
                    {account.title}
                  </button>
                )
              })}
            </div>
          </Field>

          {/* ТЗ Б.3: личный график сотрудника. Ставит руководитель, сотрудник видит
              его у себя в профиле; «на смене» считает сервер в поясе организации. */}
          <Card>
            <Switch
              checked={schedule.enabled}
              onChange={(enabled) => setSchedule((prev) => ({ ...prev, enabled }))}
              label="График работы"
              hint={
                schedule.enabled
                  ? 'Сотрудник видит смену в своём профиле'
                  : 'Без графика сотрудник считается доступным всегда'
              }
            />
            {schedule.enabled && (
              <div className="mt-3 flex flex-col gap-3">
                <Field label="Дни недели" group>
                  <div className="flex flex-wrap gap-1.5">
                    {WEEK_DAYS.map(({ value: day, short }) => {
                      const checked = schedule.days.includes(day)
                      return (
                        <button
                          key={day}
                          type="button"
                          aria-pressed={checked}
                          onClick={() =>
                            setSchedule((prev) => ({
                              ...prev,
                              days: checked
                                ? prev.days.filter((d) => d !== day)
                                : [...prev.days, day].sort((a, b) => a - b),
                            }))
                          }
                          className={`min-w-11 rounded px-2.5 py-2 text-xs transition-colors ${
                            checked
                              ? 'bg-accent-soft text-accent-text'
                              : 'bg-surface-raised text-ink-muted'
                          }`}
                        >
                          {short}
                        </button>
                      )
                    })}
                  </div>
                </Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Начало">
                    <Input
                      type="time"
                      value={schedule.start}
                      onChange={(event) =>
                        setSchedule((prev) => ({ ...prev, start: event.target.value }))
                      }
                    />
                  </Field>
                  <Field label="Конец">
                    <Input
                      type="time"
                      value={schedule.end}
                      onChange={(event) =>
                        setSchedule((prev) => ({ ...prev, end: event.target.value }))
                      }
                    />
                  </Field>
                </div>
                <p className="text-micro text-ink-faint">
                  {schedule.end <= schedule.start
                    ? 'Ночная смена: заканчивается на следующий день.'
                    : 'Время в часовом поясе организации из настроек системы.'}
                </p>
              </div>
            )}
          </Card>

          <Card>
            <Switch
              checked={active}
              onChange={setActive}
              disabled={isSelf}
              label="Активен"
              hint={
                isSelf
                  ? 'Нельзя отключить собственную учётную запись'
                  : 'Отключённый сотрудник теряет доступ сразу, его сессии завершаются'
              }
            />
          </Card>

          {member.invite_pending && (
            <div className="flex flex-col gap-2">
              <Button
                variant="secondary"
                size="sm"
                loading={resend.isPending}
                onClick={async () => {
                  setError(null)
                  try {
                    const result = await resend.mutateAsync(member.id)
                    setInviteUrl(result.invite_url)
                  } catch (cause) {
                    setError(errorMessage(cause))
                  }
                }}
              >
                <Mail className="size-4" aria-hidden />
                Отправить приглашение повторно
              </Button>
              {inviteUrl && <InviteLink url={inviteUrl} />}
            </div>
          )}

          {error && <InlineError message={error} />}
        </div>
      )}
    </Sheet>
  )
}
