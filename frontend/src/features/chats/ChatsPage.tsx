import { ChatsLayout } from '@/features/chats/ChatsLayout'

/** Раздел «Чаты» без выбранного диалога. */
export function ChatsPage() {
  return <ChatsLayout selectedId={null} />
}
