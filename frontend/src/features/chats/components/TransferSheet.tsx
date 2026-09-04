import { UserCheck } from 'lucide-react'
import { useState } from 'react'

import type { Conversation } from '@/entities/types'
import { ApiError } from '@/shared/api/client'
import { useMe } from '@/shared/hooks/useAuth'
import { Avatar, EmptyState, InlineError, ListSkeleton, Sheet } from '@/shared/ui'
import { useTransfer } from '@/features/chats/queries'
import { useAccounts } from '@/features/profile/queries'

/**
 * «Передать» диалог другому менеджеру — кнопка с доски.
 *
 * Выбор ограничен теми, кто назначен на этот аккаунт: сервер откажет остальным,
 * и показывать их в списке значило бы предлагать заведомо неработающее действие.
 */
export function TransferSheet({
  open,
  onOpenChange,
  conversation,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  conversation: Conversation
}) {
  const me = useMe()
  const accounts = useAccounts()
  const transfer = useTransfer(conversation.id)
  const [error, setError] = useState<string | null>(null)

  const account = (accounts.data ?? []).find((a) => a.id === conversation.account.id)
  const candidates = (account?.managers ?? []).filter(
    (manager) => manager.id !== conversation.responsible?.id,
  )

  async function pick(userId: number) {
    setError(null)
    try {
      await transfer.mutateAsync(userId)
      onOpenChange(false)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось передать диалог')
    }
  }

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Передать диалог"
      description={
        conversation.responsible
          ? `Сейчас ведёт ${conversation.responsible.full_name}`
          : 'Пока без ответственного'
      }
    >
      <div className="flex flex-col gap-3">
        {accounts.isLoading ? (
          <ListSkeleton rows={3} />
        ) : candidates.length === 0 ? (
          <EmptyState
            title="Передать некому"
            hint={`На аккаунт «${conversation.account.title}» не назначен другой менеджер. Назначить может руководитель.`}
            icon={<UserCheck className="size-7" aria-hidden />}
          />
        ) : (
          <ul className="flex flex-col gap-1.5">
            {candidates.map((manager) => (
              <li key={manager.id}>
                <button
                  onClick={() => void pick(manager.id)}
                  disabled={transfer.isPending}
                  className="flex w-full items-center gap-3 rounded-md bg-surface-raised px-3 py-3 text-left transition-colors hover:bg-line disabled:opacity-45"
                >
                  <Avatar
                    name={manager.full_name}
                    color={manager.avatar_color}
                    online={manager.online}
                  />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate text-sm text-ink">
                      {manager.full_name}
                      {manager.id === me.id ? ' · вы' : ''}
                    </span>
                    <span className="text-micro text-ink-faint">
                      {manager.online ? 'в сети' : 'не в сети'}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}

        {error && <InlineError message={error} />}

        <p className="text-xs text-ink-faint">
          Диалог перейдёт выбранному менеджеру. История и оплаты остаются на месте, продажи
          прошлого ответственного в статистике не меняются.
        </p>
      </div>
    </Sheet>
  )
}
