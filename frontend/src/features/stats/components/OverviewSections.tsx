import type { StatsOverview } from '@/entities/types'
import { durationLabel, money, moneyWhole, plural } from '@/shared/lib/format'
import { SectionTitle, StatTile } from '@/shared/ui'
import { Delta, pct } from '@/features/stats/components/Delta'

/**
 * Плитки сводки, сгруппированные по смыслу: продажи, клиенты, скорость ответа.
 * Подпись у каждой плитки — полным текстом: цифра без объяснения, что она
 * значит, в статистике хуже её отсутствия.
 */
export function SalesTiles({ data }: { data: StatsOverview }) {
  return (
    <div className="grid grid-cols-2 gap-2 desk:grid-cols-4">
      <StatTile
        value={money(data.sales_amount)}
        label="сумма продаж"
        tone="success"
        delta={<Delta percent={data.sales_amount_delta_pct} />}
      />
      <StatTile
        value={data.sales_count}
        label="продаж"
        delta={<Delta count={data.sales_count_delta} />}
      />
      <StatTile
        value={moneyWhole(data.avg_check)}
        label="средний чек"
        delta={<Delta percent={data.avg_check_delta_pct} />}
      />
      <StatTile
        value={pct(data.invoice_conversion_pct)}
        label={
          data.invoices_sent
            ? `счетов оплачено: ${data.invoices_paid} из ${data.invoices_sent} выставленных`
            : 'конверсия счетов — счетов в периоде не выставляли'
        }
        tone="accent"
      />
    </div>
  )
}

export function ClientTiles({ data }: { data: StatsOverview }) {
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>Клиенты и работа</SectionTitle>
      <div className="grid grid-cols-2 gap-2 desk:grid-cols-4">
        <StatTile value={data.new_clients} label="новых клиентов" />
        <StatTile
          value={pct(data.new_clients_conversion_pct)}
          label={
            data.new_clients
              ? `новых купили: ${data.new_clients_paying} из ${data.new_clients}`
              : 'конверсия новых клиентов'
          }
          tone="accent"
        />
        <StatTile value={data.active_conversations} label="чатов в работе" />
        <StatTile
          value={money(data.awaiting_amount)}
          label={`ждут оплаты · ${data.awaiting_count} ${plural(data.awaiting_count, 'сделка', 'сделки', 'сделок')} на сегодня`}
          tone="accent"
        />
      </div>
    </section>
  )
}

export function SpeedTiles({ data }: { data: StatsOverview }) {
  const goalMet =
    data.avg_response_seconds !== null &&
    data.avg_response_seconds <= data.response_goal_minutes * 60
  return (
    <section className="flex flex-col gap-2">
      <SectionTitle>Скорость ответа</SectionTitle>
      <div className="grid grid-cols-2 gap-2 desk:grid-cols-4">
        <StatTile
          value={durationLabel(data.avg_response_seconds)}
          label={`среднее время ответа · цель до ${data.response_goal_minutes} мин`}
          tone={data.avg_response_seconds === null ? 'neutral' : goalMet ? 'success' : 'warning'}
        />
        <StatTile
          value={pct(data.in_goal_pct)}
          label={
            data.waits_decided
              ? `ответили вовремя: ${data.waits_in_goal} из ${data.waits_decided} · до ${data.response_goal_minutes} мин`
              : `ответили вовремя · до ${data.response_goal_minutes} мин`
          }
          tone={
            data.in_goal_pct === null ? 'neutral' : data.in_goal_pct >= 90 ? 'success' : 'warning'
          }
        />
        <StatTile
          value={data.late_count}
          label={`просрочек — ответ позже ${data.late_threshold_minutes} мин или его нет`}
          tone={data.late_count > 0 ? 'warning' : 'neutral'}
        />
        <StatTile
          value={data.awaiting_reply_now}
          label={
            data.awaiting_reply_over_threshold
              ? `ждут ответа сейчас · ${data.awaiting_reply_over_threshold} дольше ${data.late_threshold_minutes} мин`
              : 'ждут ответа сейчас'
          }
          tone={data.awaiting_reply_over_threshold > 0 ? 'warning' : 'neutral'}
        />
      </div>
    </section>
  )
}
