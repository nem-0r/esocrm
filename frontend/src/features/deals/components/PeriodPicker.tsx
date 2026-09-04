/** Период списка оплат: пресеты и свой диапазон. */

import { Field, Input, Segmented } from '@/shared/ui'
import { daysAgo, isoDate } from '@/shared/lib/format'

export type PeriodPreset = 'week' | 'month' | 'quarter' | 'year' | 'custom'

export interface DateRange {
  from: string
  to: string
}

const PRESET_DAYS: Record<Exclude<PeriodPreset, 'custom'>, number> = {
  week: 6,
  month: 29,
  quarter: 89,
  year: 364,
}

export function presetRange(preset: PeriodPreset, current?: DateRange): DateRange {
  if (preset === 'custom') return current ?? presetRange('month')
  return { from: daysAgo(PRESET_DAYS[preset]), to: isoDate(new Date()) }
}

export function PeriodPicker({
  preset,
  range,
  onPresetChange,
  onRangeChange,
}: {
  preset: PeriodPreset
  range: DateRange
  onPresetChange: (preset: PeriodPreset) => void
  onRangeChange: (range: DateRange) => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <Segmented
        value={preset}
        onChange={onPresetChange}
        options={[
          { value: 'week', label: 'Неделя' },
          { value: 'month', label: 'Месяц' },
          { value: 'quarter', label: 'Квартал' },
          { value: 'year', label: 'Год' },
          { value: 'custom', label: 'Свой' },
        ]}
      />

      {preset === 'custom' && (
        <div className="grid grid-cols-2 gap-2">
          <Field label="С даты">
            <Input
              type="date"
              value={range.from}
              max={range.to}
              onChange={(e) => onRangeChange({ ...range, from: e.target.value })}
            />
          </Field>
          <Field label="По дату">
            <Input
              type="date"
              value={range.to}
              min={range.from}
              onChange={(e) => onRangeChange({ ...range, to: e.target.value })}
            />
          </Field>
        </div>
      )}
    </div>
  )
}
