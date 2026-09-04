import { useState, type FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/hooks/useAuth'
import { Button, Field, Input, InlineError, LoadingState } from '@/shared/ui'

export function LoginPage() {
  const { me, isLoading, login } = useAuth()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  if (isLoading) return <LoadingState className="h-dvh" />
  if (me) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from ?? '/chats'} replace />
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setPending(true)
    try {
      await login(email.trim(), password)
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось войти')
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex h-dvh items-center justify-center px-5">
      <div className="flex w-full max-w-sm flex-col gap-6">
        <header className="flex flex-col items-center gap-2 text-center">
          <span className="text-3xl" aria-hidden>
            🌙
          </span>
          <h1 className="text-2xl font-semibold text-ink">Астра CRM</h1>
          <p className="text-sm text-ink-muted">Войдите рабочей почтой</p>
        </header>

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label="Рабочая почта">
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              placeholder="ivan@astra.ru"
              required
              autoFocus
            />
          </Field>
          <Field label="Пароль">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>

          {error && <InlineError message={error} />}

          <Button type="submit" size="lg" fullWidth loading={pending}>
            Войти
          </Button>
        </form>

        <p className="text-center text-xs text-ink-faint">
          Доступ выдаёт руководитель. Если приглашение не пришло — попросите отправить повторно.
        </p>
      </div>
    </div>
  )
}
