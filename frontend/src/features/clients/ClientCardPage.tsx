import { Download, FileText, MessageSquare, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import type { BirthTimeApprox, ClientCard } from '@/entities/types'
import { dateFull, dateShort, fileSize, money, plural } from '@/shared/lib/format'
import { useMe } from '@/shared/hooks/useAuth'
import {
  Avatar,
  BackButton,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  ListSkeleton,
  Segmented,
  Select,
  Sheet,
  Textarea,
} from '@/shared/ui'
import { ClientsLayout } from '@/features/clients/ClientsLayout'
import { OverviewTab } from '@/features/clients/components/OverviewTab'
import { DealSheet } from '@/features/deals/DealSheet'
import { BIRTH_TIME_APPROX, DEAL_STATUS, errorText } from '@/features/clients/clientText'
import {
  useClient,
  useClientDeals,
  useClientMaterials,
  useClientNotes,
  useCreateNote,
  useDeleteNote,
  useUpdateClient,
  type ClientPatch,
} from '@/features/clients/queries'
import { daysAgo } from '@/shared/lib/format'

type Tab = 'overview' | 'deals' | 'materials' | 'chats' | 'notes'

const APPROX_OPTIONS = (Object.keys(BIRTH_TIME_APPROX) as BirthTimeApprox[]).map((value) => ({
  value,
  label: BIRTH_TIME_APPROX[value],
}))

export function ClientCardPage() {
  const { clientId } = useParams<{ clientId: string }>()
  const id = Number(clientId)

  if (!Number.isFinite(id)) {
    return (
      <ClientsLayout selectedId={null}>
        <EmptyState title="Клиент не найден" />
      </ClientsLayout>
    )
  }

  return (
    <ClientsLayout selectedId={id}>
      <ClientPane clientId={id} />
    </ClientsLayout>
  )
}

function ClientPane({ clientId }: { clientId: number }) {
  const card = useClient(clientId)
  const [tab, setTab] = useState<Tab>('overview')
  const [editing, setEditing] = useState(false)

  if (card.isLoading) return <ListSkeleton rows={5} />
  if (card.error) return <ErrorState error={card.error} onRetry={() => void card.refetch()} />
  if (!card.data) return <EmptyState title="Клиент не найден" />

  const client = card.data

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-start gap-3">
          {/* ТЗ п. 1.2: из карточки клиента нужен явный выход назад. */}
          <BackButton fallback="/clients" className="-ml-1 mt-0.5" />
          <Avatar name={client.name} size="lg" />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <h1 className="truncate text-lg font-semibold text-ink">{client.name}</h1>
            <span className="tnum text-xs text-ink-faint">
              id {client.id} · клиент с {dateShort(client.first_contact_at)}
            </span>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {client.paid_count > 0 ? (
                <Badge tone="success">
                  {money(client.paid_amount)} · {client.paid_count}{' '}
                  {plural(client.paid_count, 'оплата', 'оплаты', 'оплат')}
                </Badge>
              ) : (
                <Badge>нет оплат</Badge>
              )}
              {client.awaiting_amount > 0 && (
                <Badge tone="accent">ждёт оплаты {money(client.awaiting_amount)}</Badge>
              )}
              {!client.data_complete && <Badge tone="warning">неполные данные</Badge>}
            </div>
          </div>
        </div>

        <Segmented
          className="mt-3"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'overview', label: 'Обзор' },
            { value: 'deals', label: 'Оплаты' },
            { value: 'materials', label: 'Материалы' },
            { value: 'chats', label: 'Чаты', count: client.conversations.length },
            { value: 'notes', label: 'Заметки' },
          ]}
        />
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {tab === 'overview' && <OverviewTab client={client} onEdit={() => setEditing(true)} />}
        {tab === 'deals' && <DealsTab client={client} />}
        {tab === 'materials' && <MaterialsTab clientId={clientId} />}
        {tab === 'chats' && <ChatsTab client={client} />}
        {tab === 'notes' && <NotesTab clientId={clientId} />}
      </div>

      <EditSheet
        open={editing}
        onOpenChange={setEditing}
        clientId={clientId}
        initial={{
          display_name: client.display_name ?? '',
          phone: client.phone ?? '',
          birth_date: client.birth_date ?? '',
          birth_time: client.birth_time?.slice(0, 5) ?? '',
          birth_time_approx: client.birth_time_approx ?? 'none',
          birth_city: client.birth_city ?? '',
        }}
      />
    </div>
  )
}

function DealsTab({ client }: { client: ClientCard }) {
  const deals = useClientDeals(client.id, daysAgo(365))
  const items = deals.data?.pages.flatMap((page) => page.items) ?? []
  const [creating, setCreating] = useState(false)

  // Счёт уходит в чат, поэтому без единого чата создавать оплату не из чего.
  const chats = client.conversations.map((c) => ({
    id: c.id,
    label: `${c.account.title}${c.responsible ? ` · ведёт ${c.responsible.full_name}` : ''}`,
  }))

  if (deals.isLoading) return <ListSkeleton rows={4} />
  if (deals.error) return <ErrorState error={deals.error} onRetry={() => void deals.refetch()} />
  const createButton =
    chats.length > 0 ? (
      <Button size="sm" variant="secondary" onClick={() => setCreating(true)}>
        <Plus className="size-4" aria-hidden />
        Создать оплату
      </Button>
    ) : null

  const sheet = chats.length > 0 && (
    <DealSheet
      open={creating}
      onOpenChange={setCreating}
      conversationId={chats[0].id}
      clientName={client.name}
      conversations={chats}
    />
  )

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3">
        <EmptyState
          title="Оплат пока нет"
          hint={
            chats.length > 0
              ? 'Создайте оплату — счёт уйдёт в чат с клиентом.'
              : 'Счёт отправляется в чат, а чатов с этим клиентом пока нет.'
          }
        />
        {createButton}
        {sheet}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {createButton && <div className="flex justify-end">{createButton}</div>}
      {sheet}
      <ul className="flex flex-col gap-2">
      {items.map((deal) => {
        const status = DEAL_STATUS[deal.status]
        return (
          <li key={deal.id}>
            <Link
              to={`/payments/${deal.id}`}
              className="flex items-center gap-3 rounded-lg bg-surface px-4 py-3 transition-colors hover:bg-surface-raised"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate text-sm font-medium text-ink">{deal.title}</span>
                {/* ТЗ п. 4.6: в карточке клиента видны все оплаты, и у каждой
                    указан канал — через какой аккаунт она прошла. */}
                <span className="tnum text-micro text-ink-faint">
                  {deal.number} · {dateShort(deal.created_at)} · {deal.account.title}
                </span>
              </div>
              <span className="tnum shrink-0 text-sm font-semibold text-ink">
                {money(deal.total_amount)}
              </span>
              <Badge tone={status.tone}>{status.label}</Badge>
            </Link>
          </li>
        )
      })}
      </ul>
    </div>
  )
}

function MaterialsTab({ clientId }: { clientId: number }) {
  const materials = useClientMaterials(clientId)
  const items = materials.data?.pages.flatMap((page) => page.items) ?? []

  if (materials.isLoading) return <ListSkeleton rows={4} />
  if (materials.error) {
    return <ErrorState error={materials.error} onRetry={() => void materials.refetch()} />
  }
  if (items.length === 0) {
    return (
      <EmptyState
        title="Материалов пока нет"
        hint="Файлы, отправленные клиенту в чате, появятся здесь."
        icon={<FileText className="size-7" aria-hidden />}
      />
    )
  }

  return (
    <ul className="flex flex-col gap-2">
      {items.map((file) => (
        <li key={file.id}>
          <a
            href={file.url}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-3 rounded-lg bg-surface px-4 py-3 transition-colors hover:bg-surface-raised"
          >
            <FileText className="size-5 shrink-0 text-accent-text" aria-hidden />
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="truncate text-sm text-ink">{file.file_name}</span>
              <span className="text-micro text-ink-faint">
                {fileSize(file.size_bytes)} · {dateShort(file.created_at)}
                {file.author ? ` · ${file.author.full_name}` : ''}
              </span>
            </div>
            <Download className="size-4 shrink-0 text-ink-faint" aria-hidden />
          </a>
        </li>
      ))}
    </ul>
  )
}

function ChatsTab({ client }: { client: ReturnType<typeof useClient>['data'] & object }) {
  const me = useMe()
  if (client.conversations.length === 0) {
    return <EmptyState title="Диалогов нет" icon={<MessageSquare className="size-7" aria-hidden />} />
  }
  return (
    <ul className="flex flex-col gap-2">
      {client.conversations.map((conversation) => (
        <li key={conversation.id}>
          <Link
            to={`/chats/${conversation.id}`}
            className="flex items-center gap-3 rounded-lg bg-surface px-4 py-3 transition-colors hover:bg-surface-raised"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="truncate text-sm font-medium text-ink">
                {conversation.account.title}
              </span>
              <span className="text-micro text-ink-faint">
                {conversation.responsible
                  ? conversation.responsible.id === me.id
                    ? 'Ваш чат'
                    : `Ведёт ${conversation.responsible.full_name}`
                  : 'Без ответственного'}
                {conversation.last_message_at
                  ? ` · ${dateShort(conversation.last_message_at)}`
                  : ''}
              </span>
            </div>
            {conversation.unread_count > 0 && (
              <span className="tnum flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1.5 text-micro font-semibold text-white">
                {conversation.unread_count}
              </span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  )
}

function NotesTab({ clientId }: { clientId: number }) {
  const me = useMe()
  const notes = useClientNotes(clientId)
  const noteRows = notes.data?.pages.flatMap((page) => page.items) ?? []
  const create = useCreateNote(clientId)
  const remove = useDeleteNote(clientId)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    const value = text.trim()
    if (!value) return
    setError(null)
    try {
      await create.mutateAsync(value)
      setText('')
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <Textarea
          rows={3}
          value={text}
          maxLength={2000}
          onChange={(event) => setText(event.target.value)}
          placeholder="Что важно помнить об этом клиенте"
        />
        {error && <span className="text-xs text-danger">{error}</span>}
        <Button
          size="sm"
          className="self-end"
          disabled={!text.trim()}
          loading={create.isPending}
          onClick={() => void submit()}
        >
          <Plus className="size-4" aria-hidden />
          Добавить заметку
        </Button>
      </Card>

      {notes.isLoading ? (
        <ListSkeleton rows={3} />
      ) : notes.error ? (
        <ErrorState error={notes.error} onRetry={() => void notes.refetch()} />
      ) : noteRows.length === 0 ? (
        <EmptyState title="Заметок пока нет" />
      ) : (
        <ul className="flex flex-col gap-2">
          {noteRows.map((note) => (
            <li key={note.id} className="flex flex-col gap-2 rounded-lg bg-surface p-4">
              <p className="whitespace-pre-wrap text-sm text-ink">{note.text}</p>
              <div className="flex items-center gap-2">
                <Avatar
                  name={note.author.full_name}
                  color={note.author.avatar_color}
                  size="sm"
                />
                <span className="text-micro text-ink-faint">
                  {note.author.full_name} · {dateFull(note.created_at)}
                </span>
                {note.author.id === me.id && (
                  <button
                    aria-label="Удалить заметку"
                    onClick={() => remove.mutate(note.id)}
                    className="ml-auto text-ink-faint transition-colors hover:text-danger"
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function EditSheet({
  open,
  onOpenChange,
  clientId,
  initial,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  clientId: number
  initial: Record<string, string>
}) {
  const update = useUpdateClient(clientId)
  const [form, setForm] = useState(initial)
  const [error, setError] = useState<string | null>(null)

  function set(key: string, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function save() {
    setError(null)
    const patch: ClientPatch = {
      display_name: form.display_name.trim() || null,
      phone: form.phone.trim() || null,
      birth_date: form.birth_date || null,
      birth_time: form.birth_time || null,
      birth_time_approx:
        form.birth_time || form.birth_time_approx === 'none'
          ? null
          : (form.birth_time_approx as BirthTimeApprox),
      birth_city: form.birth_city.trim() || null,
    }
    try {
      await update.mutateAsync(patch)
      onOpenChange(false)
    } catch (cause) {
      setError(errorText(cause))
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Данные клиента"
      description="Изменения сохранятся после нажатия кнопки"
      footer={
        <Button fullWidth loading={update.isPending} onClick={() => void save()}>
          Сохранить
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {/* ТЗ п. 5.3: клиент может не указать в Telegram ни имени, ни @username.
            Менеджер вписывает имя руками, и оно заменяет подпись сверху для всех. */}
        <Field
          label="Имя"
          hint="Заменит @username или id в шапке карточки — увидят все сотрудники"
        >
          <Input
            value={form.display_name}
            onChange={(event) => set('display_name', event.target.value)}
            placeholder="Как обращаться к клиенту"
          />
        </Field>
        <Field label="Телефон" hint="Цифры, плюс, пробелы и дефисы">
          <Input value={form.phone} onChange={(event) => set('phone', event.target.value)} />
        </Field>
        <Field label="Дата рождения">
          <Input
            type="date"
            value={form.birth_date}
            onChange={(event) => set('birth_date', event.target.value)}
          />
        </Field>
        <Field label="Время рождения" hint="Нужно для точного расчёта карты">
          <Input
            type="time"
            value={form.birth_time}
            onChange={(event) => set('birth_time', event.target.value)}
          />
        </Field>
        {/* Часть суток спрашиваем только пока точного времени нет: оно точнее,
            и держать оба ответа одновременно незачем. */}
        {!form.birth_time && (
          <Field label="Если точное время неизвестно" hint="Хотя бы часть суток">
            <Select
              value={form.birth_time_approx as BirthTimeApprox | 'none'}
              onChange={(value) => set('birth_time_approx', value)}
              options={[
                { value: 'none', label: 'Не указано' },
                ...APPROX_OPTIONS,
              ]}
            />
          </Field>
        )}
        <Field label="Город рождения">
          <Input
            value={form.birth_city}
            onChange={(event) => set('birth_city', event.target.value)}
          />
        </Field>
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </Sheet>
  )
}
