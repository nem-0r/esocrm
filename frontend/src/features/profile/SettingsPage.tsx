import { Check } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { Settings } from '@/entities/types'
import {
  Button,
  Card,
  ErrorState,
  Field,
  InlineError,
  Input,
  ListSkeleton,
  SectionTitle,
  Select,
  Switch,
} from '@/shared/ui'
import { ProfileScreen } from '@/features/profile/components/ProfileScreen'
import {
  ROBOKASSA_SNO_OPTIONS,
  ROBOKASSA_TAX_OPTIONS,
  TIMEZONES,
  WEEK_DAYS,
  errorMessage,
  fieldErrors,
} from '@/features/profile/lib'
import { useSettings, useUpdateSettings } from '@/features/profile/queries'

export function SettingsPage() {
  const settings = useSettings()
  const update = useUpdateSettings()
  const [form, setForm] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fields, setFields] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (settings.data) setForm(settings.data)
  }, [settings.data])

  if (settings.isLoading || !form) {
    return (
      <ProfileScreen title="Настройки системы" backTo="/profile">
        <ListSkeleton rows={5} />
      </ProfileScreen>
    )
  }

  if (settings.error) {
    return (
      <ProfileScreen title="Настройки системы" backTo="/profile">
        <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
      </ProfileScreen>
    )
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev))
    setSaved(false)
  }

  async function save() {
    if (!form) return
    setError(null)
    setFields({})
    try {
      await update.mutateAsync(form)
      setSaved(true)
    } catch (cause) {
      setError(errorMessage(cause))
      setFields(fieldErrors(cause))
    }
  }

  return (
    <ProfileScreen
      title="Настройки системы"
      subtitle="Меняются без релиза и действуют сразу"
      backTo="/profile"
    >
      <div className="flex flex-col gap-4">
        <Card>
          <SectionTitle>Скорость ответа</SectionTitle>
          <Field
            label="Порог красного баннера, минут"
            hint="Сколько клиент может ждать, прежде чем чат подсветится"
            error={fields.awaiting_banner_minutes}
          >
            <Input
              type="number"
              min={1}
              max={1440}
              value={form.awaiting_banner_minutes}
              onChange={(event) => set('awaiting_banner_minutes', Number(event.target.value))}
            />
          </Field>
          <Field
            label="Цель по времени ответа, минут"
            hint="Показывается в статистике рядом со средним временем"
            error={fields.response_time_goal_minutes}
          >
            <Input
              type="number"
              min={1}
              max={1440}
              value={form.response_time_goal_minutes}
              onChange={(event) => set('response_time_goal_minutes', Number(event.target.value))}
            />
          </Field>
        </Card>

        <Card>
          <SectionTitle>Рабочие часы</SectionTitle>
          <Switch
            checked={form.working_hours_enabled}
            onChange={(value) => set('working_hours_enabled', value)}
            label="Учитывать рабочие часы"
            hint={
              form.working_hours_enabled
                ? 'Ожидание клиента и время ответа считаются только в рабочие часы'
                : 'Выключено: ожидание считается круглосуточно, ночь портит метрику'
            }
          />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Начало" error={fields.working_hours_start}>
              <Input
                type="time"
                disabled={!form.working_hours_enabled}
                value={form.working_hours_start}
                onChange={(event) => set('working_hours_start', event.target.value)}
              />
            </Field>
            <Field label="Конец" error={fields.working_hours_end}>
              <Input
                type="time"
                disabled={!form.working_hours_enabled}
                value={form.working_hours_end}
                onChange={(event) => set('working_hours_end', event.target.value)}
              />
            </Field>
          </div>
          <Field label="Рабочие дни" error={fields.working_days}>
            <div className="flex gap-1.5">
              {WEEK_DAYS.map((day) => {
                const checked = form.working_days.includes(day.value)
                return (
                  <button
                    key={day.value}
                    disabled={!form.working_hours_enabled}
                    onClick={() =>
                      set(
                        'working_days',
                        checked
                          ? form.working_days.filter((d) => d !== day.value)
                          : [...form.working_days, day.value].sort(),
                      )
                    }
                    className={`size-9 rounded text-xs transition-colors disabled:opacity-45 ${
                      checked ? 'bg-accent-soft text-accent-text' : 'bg-surface-raised text-ink-muted'
                    }`}
                  >
                    {day.short}
                  </button>
                )
              })}
            </div>
          </Field>
        </Card>

        <Card>
          <SectionTitle>Оплаты</SectionTitle>
          <Field
            label="Срок действия оплаты, дней"
            hint="Через сколько сделка помечается истёкшей"
            error={fields.deal_link_ttl_days}
          >
            <Input
              type="number"
              min={1}
              max={90}
              value={form.deal_link_ttl_days}
              onChange={(event) => set('deal_link_ttl_days', Number(event.target.value))}
            />
          </Field>
          <Field
            label="Система налогообложения"
            hint="Для чека 54-ФЗ в ссылке Робокассы — уточните у бухгалтера"
            error={fields.robokassa_sno}
          >
            <Select
              value={form.robokassa_sno}
              onChange={(value) => set('robokassa_sno', value)}
              options={ROBOKASSA_SNO_OPTIONS}
            />
          </Field>
          <Field label="Ставка НДС" error={fields.robokassa_tax}>
            <Select
              value={form.robokassa_tax}
              onChange={(value) => set('robokassa_tax', value)}
              options={ROBOKASSA_TAX_OPTIONS}
            />
          </Field>
        </Card>

        <Card>
          <SectionTitle>Организация</SectionTitle>
          <Field label="Часовой пояс" error={fields.timezone}>
            <Select
              value={form.timezone}
              onChange={(value) => set('timezone', value)}
              options={TIMEZONES}
            />
          </Field>
          <Field
            label="Тянуть историю с даты"
            hint="При подключении аккаунта подтягивается переписка начиная с этого дня"
            error={fields.history_sync_from}
          >
            <Input
              type="date"
              value={form.history_sync_from}
              onChange={(event) => set('history_sync_from', event.target.value)}
            />
          </Field>
        </Card>

        {error && <InlineError message={error} />}

        <div className="flex items-center gap-3">
          <Button loading={update.isPending} onClick={() => void save()}>
            Сохранить
          </Button>
          {saved && (
            <span className="flex items-center gap-1.5 text-xs text-success">
              <Check className="size-4" aria-hidden />
              Сохранено
            </span>
          )}
        </div>
      </div>
    </ProfileScreen>
  )
}
