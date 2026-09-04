import { PaymentsLayout } from '@/features/deals/components/PaymentsLayout'

/** Раздел «Оплаты» без открытой карточки. */
export function PaymentsPage() {
  return <PaymentsLayout selectedId={null} />
}
