/**
 * Период списка оплат: пресеты, свой диапазон и подпись с выбранными датами.
 * Сами пресеты считает `shared/lib/period` — те же, что в «Статистике» (D-39).
 */

import { Field, Input, Segmented } from '@/shared/ui'
import {
  MAX_PERIOD_DATE,
  MIN_PERIOD_DATE,
  rangeError,
  rangeLabel,
  type DateRange,
  type PeriodPreset,
} from '@/shared/lib/period'

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
  // min/max на поле работают только в календаре — вручную можно ввести что угодно,
  // поэтому период проверяется ещё и здесь, а в родителе по нему отключаются запросы.
  const error = rangeError(range.from, range.to)

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
              min={MIN_PERIOD_DATE}
              max={range.to || MAX_PERIOD_DATE}
              invalid={error !== null}
              onChange={(e) => onRangeChange({ ...range, from: e.target.value })}
            />
          </Field>
          <Field label="По дату">
            <Input
              type="date"
              value={range.to}
              min={range.from || MIN_PERIOD_DATE}
              max={MAX_PERIOD_DATE}
              invalid={error !== null}
              onChange={(e) => onRangeChange({ ...range, to: e.target.value })}
            />
          </Field>
        </div>
      )}

      {/* Подпись есть в любом режиме: видно, какие даты стоят за словом «Месяц». */}
      {error ? (
        <span role="alert" className="text-xs text-danger">
          {error}
        </span>
      ) : (
        <span aria-live="polite" className="text-xs text-ink-faint">
          {rangeLabel(range)}
        </span>
      )}
    </div>
  )
}
