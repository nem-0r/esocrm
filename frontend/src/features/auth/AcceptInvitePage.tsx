import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'

import { api, ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/hooks/useAuth'
import { Button, Field, InlineError, Input } from '@/shared/ui'

export function AcceptInvitePage() {
  const { token } = useParams<{ token: string }>()
  const { me, refresh } = useAuth()
  const navigate = useNavigate()
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  if (me) return <Navigate to="/chats" replace />

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (password.length < 8) {
      setError('Пароль должен быть не короче 8 символов')
      return
    }
    if (password !== repeat) {
      setError('Пароли не совпадают')
      return
    }
    setPending(true)
    try {
      await api.post('/auth/accept-invite', { token, password })
      refresh()
      navigate('/chats', { replace: true })
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Не удалось принять приглашение')
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
          <h1 className="text-2xl font-semibold text-ink">Задайте пароль</h1>
          <p className="text-sm text-ink-muted">
            Это последний шаг — после него откроется рабочее место.
          </p>
        </header>

        <form onSubmit={onSubmit} className="flex flex-col gap-4">
          <Field label="Новый пароль" hint="Не короче 8 символов">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
              required
              autoFocus
            />
          </Field>
          <Field label="Повторите пароль">
            <Input
              type="password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              autoComplete="new-password"
              required
            />
          </Field>

          {error && <InlineError message={error} />}

          <Button type="submit" size="lg" fullWidth loading={pending}>
            Сохранить и войти
          </Button>
        </form>
      </div>
    </div>
  )
}
