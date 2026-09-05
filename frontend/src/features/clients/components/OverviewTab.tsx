import { Pencil } from 'lucide-react'
import type { ReactNode } from 'react'
import { useState } from 'react'

import type { ClientCard } from '@/entities/types'
import { birthDate, dateFull, money, plural } from '@/shared/lib/format'
import { Badge, Card, ConfirmDialog, InlineError, SectionTitle, StatTile } from '@/shared/ui'
import { birthTimeFull } from '@/features/clients/clientText'
import { useUpdateClient } from '@/features/clients/queries'
import { errorMessage, FUNNEL_LABEL } from '@/features/profile/lib'

export function OverviewTab({ client, onEdit }: { client: ClientCard; onEdit: () => void }) {
  const hasSource = Boolean(client.source_code || client.source)
  const update = useUpdateClient(client.id)
  const [revoking, setRevoking] = useState(false)
  const [revokeError, setRevokeError] = useState<string | null>(null)

  return (
    <div className="flex flex-col gap-3 p-4">
      <Card>
        <SectionTitle
          action={
            // ТЗ п. 5.4: одной иконки мало — рядом нужна подпись «Изменить».
            <button
              onClick={onEdit}
              className="-my-1 flex min-h-9 items-center gap-1.5 rounded px-1.5 text-xs text-accent-text transition-colors hover:bg-surface-raised"
            >
              <Pencil className="size-4" aria-hidden />
              Изменить
            </button>
          }
        >
          Данные рождения
        </SectionTitle>

        {!client.data_complete && (
          <div>
            <Badge tone="warning">неполные данные</Badge>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <Row label="Дата" value={client.birth_date ? birthDate(client.birth_date) : null} />
          <Row label="Время" value={birthTimeFull(client)} />
          <Row label="Город" value={client.birth_city} />
          <Row label="Знак" value={client.zodiac_sign} />
        </div>
      </Card>

      <div className="flex flex-col gap-2">
        <SectionTitle>Финансы</SectionTitle>
        <div className="grid grid-cols-3 gap-2">
          <StatTile value={money(client.paid_amount)} label="оплачено всего" tone="success" />
          <StatTile
            value={client.paid_count}
            label={plural(client.paid_count, 'сделка', 'сделки', 'сделок')}
          />
          <StatTile
            value={money(client.awaiting_amount)}
            label="ждёт оплаты"
            tone={client.awaiting_amount > 0 ? 'accent' : 'neutral'}
          />
        </div>
      </div>

      <Card>
        <SectionTitle>Источник</SectionTitle>
        <div className="flex flex-col gap-2">
          {/* ТЗ п. 5.2: через какой аккаунт заведена карточка и на скольких
              аккаунтах клиент вообще присутствует. */}
          <Row
            label="Карточка заведена"
            value={
              client.created_via_account
                ? `${client.created_via_account.title} · ${FUNNEL_LABEL[client.created_via_account.funnel_stage]}`
                : null
            }
          />
          <Row
            label="Аккаунтов у клиента"
            value={`${client.accounts_count} ${plural(client.accounts_count, 'аккаунт', 'аккаунта', 'аккаунтов')}`}
          />
          {hasSource ? (
            <>
              <Row label="Код" value={client.source_code} />
              <Row label="Источник" value={client.source} />
            </>
          ) : (
            <Row label="Код источника" value={null} />
          )}
        </div>
      </Card>

      <Card>
        <SectionTitle>Контакты</SectionTitle>
        <div className="flex flex-col gap-2">
          <Row label="Телефон" value={client.phone} />
          <Row
            label="Telegram"
            value={client.tg_username ? `@${client.tg_username}` : null}
          />
        </div>
      </Card>

      <Card>
        <SectionTitle>Согласия</SectionTitle>
        <div className="flex flex-col gap-2">
          <Row
            label="Обработка ПДн"
            value={
              client.pdn_consent_at
                ? `дано ${dateFull(client.pdn_consent_at)}${
                    client.pdn_consent_version ? ` · версия ${client.pdn_consent_version}` : ''
                  }`
                : null
            }
          />
          <Row
            label="Рассылки"
            value={
              <div className="flex items-center gap-2">
                <Badge tone={client.marketing_consent ? 'success' : 'danger'}>
                  {client.marketing_consent ? 'разрешены' : 'клиент отказался'}
                </Badge>
                {client.marketing_consent && (
                  <button
                    onClick={() => setRevoking(true)}
                    className="text-xs text-accent-text underline-offset-4 transition-colors hover:underline"
                  >
                    Отозвать
                  </button>
                )}
              </div>
            }
          />
          {revokeError && <InlineError message={revokeError} />}
        </div>
      </Card>

      <ConfirmDialog
        open={revoking}
        onOpenChange={setRevoking}
        title="Отозвать согласие на рассылку"
        message="Используйте только по просьбе клиента — согласие фиксируется как отозванное, с датой. Выдать его заново клиент сможет только сам."
        confirmLabel="Отозвать"
        danger
        loading={update.isPending}
        onConfirm={async () => {
          setRevokeError(null)
          try {
            await update.mutateAsync({ marketing_consent: false })
            setRevoking(false)
          } catch (cause) {
            setRevokeError(errorMessage(cause))
          }
        }}
      />
    </div>
  )
}

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="shrink-0 text-xs text-ink-muted">{label}</span>
      <span className="tnum min-w-0 truncate text-right text-sm text-ink">
        {value === null || value === undefined || value === '' ? (
          <span className="text-ink-faint">не указано</span>
        ) : (
          value
        )}
      </span>
    </div>
  )
}
