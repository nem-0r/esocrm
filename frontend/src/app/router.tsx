import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Suspense, lazy, type ReactNode } from 'react'

import { AppShell } from '@/app/layout/AppShell'
import { AcceptInvitePage } from '@/features/auth/AcceptInvitePage'
import { LoginPage } from '@/features/auth/LoginPage'
import { ChatPage } from '@/features/chats/ChatPage'
import { ChatsPage } from '@/features/chats/ChatsPage'
import { ClientCardPage } from '@/features/clients/ClientCardPage'
import { ClientsPage } from '@/features/clients/ClientsPage'
import { DealCardPage } from '@/features/deals/DealCardPage'
import { PaymentsPage } from '@/features/deals/PaymentsPage'
import { PayFailPage } from '@/features/pay/PayFailPage'
import { PaySuccessPage } from '@/features/pay/PaySuccessPage'
import { ProfilePage } from '@/features/profile/ProfilePage'
// Отдельными частями сборки: графики и админские экраны нужны не каждому
// и не в первую секунду. Загружаются при первом заходе в раздел.
const StatsPage = lazy(() =>
  import('@/features/stats/StatsPage').then((m) => ({ default: m.StatsPage })),
)
const SearchPage = lazy(() =>
  import('@/features/search/SearchPage').then((m) => ({ default: m.SearchPage })),
)
const AccountsPage = lazy(() =>
  import('@/features/profile/AccountsPage').then((m) => ({ default: m.AccountsPage })),
)
const StaffPage = lazy(() =>
  import('@/features/profile/StaffPage').then((m) => ({ default: m.StaffPage })),
)
const SettingsPage = lazy(() =>
  import('@/features/profile/SettingsPage').then((m) => ({ default: m.SettingsPage })),
)

import { useAuth } from '@/shared/hooks/useAuth'
import { LoadingState, ErrorState, EmptyState } from '@/shared/ui'

function Protected({ children }: { children: ReactNode }) {
  const { me, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) return <LoadingState className="h-dvh" />
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <>{children}</>
}

function AdminOnly({ children }: { children: ReactNode }) {
  const { me } = useAuth()
  if (me?.role !== 'admin') {
    return (
      <EmptyState
        title="Раздел доступен только руководителю"
        hint="Если вам нужен доступ, попросите руководителя изменить вашу роль."
      />
    )
  }
  return <>{children}</>
}

export function AppRoutes() {
  const { error, isLoading, me } = useAuth()

  // Сервер недоступен — показываем это явно, а не пустой экран.
  if (!isLoading && !me && error && (error as { status?: number }).status !== 401) {
    return <ErrorState error={error} onRetry={() => window.location.reload()} className="h-dvh" />
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/invite/:token" element={<AcceptInvitePage />} />
      {/* Редиректы Робокассы: клиент не сотрудник CRM, страницы без авторизации
          и без обращений к бэкенду (docs/11-payments-architecture.md, §5). */}
      <Route path="/pay/success" element={<PaySuccessPage />} />
      <Route path="/pay/fail" element={<PayFailPage />} />

      <Route
        element={
          <Protected>
            <Suspense fallback={<LoadingState className="h-dvh" />}>
              <AppShell />
            </Suspense>
          </Protected>
        }
      >
        <Route path="/chats" element={<ChatsPage />} />
        <Route path="/chats/:conversationId" element={<ChatPage />} />
        <Route path="/clients" element={<ClientsPage />} />
        <Route path="/clients/:clientId" element={<ClientCardPage />} />
        <Route path="/payments" element={<PaymentsPage />} />
        <Route path="/payments/:dealId" element={<DealCardPage />} />
        <Route path="/stats" element={<StatsPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route
          path="/profile/accounts"
          element={
            <AdminOnly>
              <AccountsPage />
            </AdminOnly>
          }
        />
        <Route
          path="/profile/staff"
          element={
            <AdminOnly>
              <StaffPage />
            </AdminOnly>
          }
        />
        <Route
          path="/settings"
          element={
            <AdminOnly>
              <SettingsPage />
            </AdminOnly>
          }
        />
        <Route index element={<Navigate to="/chats" replace />} />
      </Route>

      <Route
        path="*"
        element={
          <EmptyState
            title="Страница не найдена"
            hint="Проверьте адрес или вернитесь к чатам."
            className="h-dvh"
          />
        }
      />
    </Routes>
  )
}
