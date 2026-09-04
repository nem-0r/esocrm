import { ClientsLayout } from '@/features/clients/ClientsLayout'

/** Раздел «Клиенты» без открытой карточки. */
export function ClientsPage() {
  return <ClientsLayout selectedId={null} />
}
