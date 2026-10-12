import {
  AlertTriangle,
  Bell,
  ChevronRight,
  KeyRound,
  Landmark,
  LogOut,
  Pencil,
  Settings as SettingsIcon,
  Smartphone,
  Tags,
  Users,
} from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { dateFull, durationLabel, money } from '@/shared/lib/format'
import { useAuth, useMe } from '@/shared/hooks/useAuth'
import {
  Avatar,
  Badge,
  Button,
  Card,
  Dot,
  EmptyState,
  ErrorState,
  InlineError,
  ListSkeleton,
  SectionTitle,
  StatTile,
  Switch,
} from '@/shared/ui'
import { NavRow, ProfileScreen } from '@/features/profile/components/ProfileScreen'
import { EditProfileSheet } from '@/features/profile/components/EditProfileSheet'
import { NotificationsSheet } from '@/features/profile/components/NotificationsSheet'
import { PasswordSheet } from '@/features/profile/components/PasswordSheet'
import { ROLE_LABEL, STATUS_LABEL, STATUS_TONE, errorMessage } from '@/features/profile/lib'
import {
  useAccounts,
  useAccountsSummary,
  useSettings,
  useUnreadCount,
  useUpdateMe,
} from '@/features/profile/queries'
import {
  acceptingLeadsHint,
  accountAttentionBanner,
  accountsWithoutLink,
} from '@/features/profile/rules'

export function ProfilePage() {
  const me = useMe()
  const { logout, isAdmin } = useAuth()
  const updateMe = useUpdateMe()
  // Подпись под переключателем зависит от того, на каких аккаунтах человек
  // работает один, — те же данные, что и в блоке «Мои аккаунты».
  const accounts = useAccounts()
  const [passwordOpen, setPasswordOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <ProfileScreen title="Профиль">
      <div className="flex flex-col gap-4">
        <Card>
          <div className="flex items-start gap-3">
            <Avatar name={me.full_name} color={me.avatar_color} size="lg" />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="truncate text-base font-semibold text-ink">{me.full_name}</span>
              <span className="truncate text-xs text-ink-muted">{me.email}</span>
              {me.phone && <span className="truncate text-xs text-ink-muted">{me.phone}</span>}
              {/* ТЗ Б.1: с какого момента человек работает. */}
              <span className="mt-1 flex flex-wrap items-center gap-1.5 text-micro text-ink-faint">
                <Dot tone="success" />в сети · {ROLE_LABEL[me.role]} · работает с{' '}
                {dateFull(me.works_since)}
              </span>
            </div>
            <button
              onClick={() => setEditOpen(true)}
              className="flex size-8 shrink-0 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
              aria-label="Изменить имя и телефон"
            >
              <Pencil className="size-4" aria-hidden />
            </button>
          </div>

          <div className="border-t border-line pt-3">
            <Switch
              checked={me.accepting_leads}
              label={me.accepting_leads ? 'Приём заявок включён' : 'Приём заявок выключен'}
              hint={acceptingLeadsHint(
                me.accepting_leads,
                accounts.data ?? null,
                me.id,
              )}
              onChange={(value) => {
                setError(null)
                updateMe.mutate(
                  { accepting_leads: value },
                  { onError: (cause) => setError(errorMessage(cause)) },
                )
              }}
            />
          </div>

          {error && <InlineError message={error} />}

          <div className="flex gap-2 border-t border-line pt-3">
            <Button variant="secondary" size="sm" onClick={() => setPasswordOpen(true)}>
              <KeyRound className="size-4" aria-hidden />
              Сменить пароль
            </Button>
            <Button variant="ghost" size="sm" className="ml-auto" onClick={() => void logout()}>
              <LogOut className="size-4" aria-hidden />
              Выйти
            </Button>
          </div>
        </Card>

        {isAdmin ? <DirectorBlocks /> : <ManagerBlocks />}

        <NotificationsBlock />
      </div>

      <EditProfileSheet me={me} open={editOpen} onOpenChange={setEditOpen} />
      <PasswordSheet open={passwordOpen} onOpenChange={setPasswordOpen} />
    </ProfileScreen>
  )
}

function ManagerBlocks() {
  const me = useMe()
  const accounts = useAccounts()
  const rows = accounts.data ?? []

  return (
    <>
      {/* ТЗ Б.2: рядом с диалогами и продажами — среднее время ответа.
          Показатели личные и за календарный месяц: ровно те же цифры, что
          руководитель видит про этого сотрудника в списке. Две разные цифры
          об одном человеке на разных экранах — то, из-за чего перестают
          верить отчётам. */}
      <div className="grid grid-cols-3 gap-2">
        <StatTile value={me.month_conversations} label="моих диалогов за месяц" />
        <StatTile
          value={
            me.avg_response_seconds === null ? '—' : durationLabel(me.avg_response_seconds)
          }
          label="среднее время ответа"
          tone={me.avg_response_seconds === null ? 'neutral' : 'accent'}
        />
        <StatTile
          value={money(me.month_sales_amount)}
          label="моих продаж за месяц"
          tone="success"
        />
      </div>

      {/* ТЗ Б.5: сколько моих аккаунтов требуют внимания — видно и менеджеру,
          не только руководителю. Менеджеру это просто сообщение: раздела
          «Аккаунты» у него нет, переподключает руководитель. */}
      <AccountAttentionBanner
        count={accounts.data ? accountsWithoutLink(accounts.data) : me.accounts_attention}
      />

      {/* ТЗ Б.3: график работы. Показываем режим организации из настроек —
          персональных смен в системе нет, и заводить их без описания было бы
          гаданием. Если нужен график на каждого сотрудника, это отдельная работа. */}
      <WorkScheduleCard />

      <Card>
        <SectionTitle>Мои аккаунты</SectionTitle>
        {accounts.isLoading ? (
          <ListSkeleton rows={2} />
        ) : accounts.error ? (
          <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Аккаунты не назначены"
            hint="Попросите руководителя назначить вас на рабочий аккаунт."
            icon={<Smartphone className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((account) => (
              <li
                key={account.id}
                className="flex items-center gap-3 rounded-md bg-surface-raised px-3 py-2.5"
              >
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-sm text-ink">{account.title}</span>
                  <span className="text-micro text-ink-faint">
                    {account.tg_username ? `@${account.tg_username} · ` : ''}
                    {account.conversations_count} диалогов
                  </span>
                </div>
                <Badge tone={STATUS_TONE[account.status]}>{STATUS_LABEL[account.status]}</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  )
}

function DirectorBlocks() {
  const summary = useAccountsSummary()

  return (
    <>
      <div className="grid grid-cols-2 gap-2 desk:grid-cols-4">
        <StatTile value={summary.data?.total ?? '—'} label="аккаунтов всего" />
        <StatTile value={summary.data?.connected ?? '—'} label="подключено" tone="success" />
        <StatTile
          value={summary.data?.attention ?? '—'}
          label="требуют внимания"
          tone={summary.data?.attention ? 'warning' : 'neutral'}
        />
        <StatTile value={summary.data?.conversations_total ?? '—'} label="диалогов всего" />
      </div>

      {/* Те же аккаунты, что и в плитке «требуют внимания»: нажатие открывает
          их список с уже включённым фильтром. */}
      <AccountAttentionBanner count={summary.data?.attention ?? 0} />

      <div className="flex flex-col gap-2">
        <NavRow
          to="/profile/accounts"
          icon={<Smartphone className="size-5" aria-hidden />}
          title="Аккаунты"
          hint="Подключение, статус, назначение менеджеров"
        />
        <NavRow
          to="/profile/staff"
          icon={<Users className="size-5" aria-hidden />}
          title="Сотрудники"
          hint="Приглашения, роли, доступ"
        />
        <NavRow
          to="/profile/services"
          icon={<Tags className="size-5" aria-hidden />}
          title="Услуги и цены"
          hint="Перечень услуг в окне оплаты"
        />
        <NavRow
          to="/profile/requisites"
          icon={<Landmark className="size-5" aria-hidden />}
          title="Реквизиты"
          hint="Счета для оплаты по реквизитам"
        />
        <NavRow
          to="/settings"
          icon={<SettingsIcon className="size-5" aria-hidden />}
          title="Настройки системы"
          hint="Пороги ответа, рабочие часы, сроки оплаты"
        />
      </div>
    </>
  )
}

/** Плашка «аккаунт требует внимания»: руководителю ссылка, менеджеру — сообщение. */
function AccountAttentionBanner({ count }: { count: number }) {
  const { isAdmin } = useAuth()
  if (count <= 0) return null
  const { text, href } = accountAttentionBanner(count, isAdmin)
  const body = (
    <>
      <AlertTriangle className="size-4 shrink-0" aria-hidden />
      <span className="flex-1">{text}</span>
      {href && <ChevronRight className="size-4 shrink-0" aria-hidden />}
    </>
  )
  const look = 'flex items-center gap-2 rounded-lg bg-warning-soft px-4 py-3 text-sm text-warning'
  return href ? (
    <Link to={href} className={`${look} transition-colors hover:bg-warning/20`}>
      {body}
    </Link>
  ) : (
    <div className={look}>{body}</div>
  )
}

function NotificationsBlock() {
  // ТЗ Б.4: список уведомлений открывается отдельным окном, которое закрывается
  // крестиком или свайпом вниз. В профиле остаётся строка со счётчиком
  // непрочитанных — иначе длинный список выдавливает всё остальное вниз.
  // То же окно открывает колокольчик в шапке остальных разделов.
  const [open, setOpen] = useState(false)
  const unread = useUnreadCount().data?.count ?? 0

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className="flex w-full items-center gap-3 rounded-lg bg-surface px-4 py-3 text-left transition-colors hover:bg-surface-raised"
      >
        <Bell className="size-5 shrink-0 text-ink-muted" aria-hidden />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-sm text-ink">Уведомления</span>
          <span className="text-micro text-ink-faint">
            {unread > 0 ? `${unread} непрочитанных` : 'все прочитаны'}
          </span>
        </span>
        {unread > 0 && <Badge tone="accent">{unread}</Badge>}
        <ChevronRight className="size-4 shrink-0 text-ink-faint" aria-hidden />
      </button>

      <NotificationsSheet open={open} onOpenChange={setOpen} />
    </>
  )
}


const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

function WorkScheduleCard() {
  const me = useMe()
  const settings = useSettings()
  const personal = me.schedule

  return (
    <Card>
      <SectionTitle>График работы</SectionTitle>
      {/* ТЗ Б.3: сначала личная смена сотрудника — она конкретнее режима компании.
          Режим компании остаётся ниже: он объясняет, в каком поясе идёт отсчёт. */}
      {personal?.enabled ? (
        <div className="flex flex-col gap-1">
          <span className="flex items-center gap-1.5 text-sm text-ink">
            <Dot tone={personal.on_shift ? 'success' : 'neutral'} />
            {personal.on_shift ? 'Смена идёт' : 'Вне смены'}
          </span>
          <span className="text-xs text-ink-muted">{personal.summary}</span>
          <span className="text-micro text-ink-faint">
            Смену ставит руководитель. Время — по поясу {settings.data?.timezone ?? 'организации'}.
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          <span className="text-sm text-ink">Личный график не задан</span>
          <span className="text-micro text-ink-faint">
            {settings.data?.working_hours_enabled
              ? `Режим компании: ${settings.data.working_hours_start.slice(0, 5)} — ` +
                `${settings.data.working_hours_end.slice(0, 5)}, ` +
                `${(settings.data.working_days ?? []).map((d) => WEEKDAYS[d - 1]).filter(Boolean).join(', ') || 'дни не заданы'}.`
              : 'Режим компании — круглосуточно.'}{' '}
            Личную смену назначает руководитель в разделе «Сотрудники».
          </span>
        </div>
      )}
    </Card>
  )
}
