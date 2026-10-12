/**
 * Страница «Новый пароль» по одноразовой ссылке руководителя (D-38).
 *
 * Почты в системе служебные, писем нет: сотрудник, забывший пароль, получает
 * от руководителя ссылку `/reset/{токен}`, задаёт здесь новый пароль и сразу
 * входит. Ссылка живёт 24 часа и срабатывает один раз — любая причина отказа
 * (просрочена, использована, выдана заново, не существует) показывается одинаково.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api, ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/hooks/useAuth'
import { dateTimeFull } from '@/shared/lib/format'
import { Button, EmptyState, ErrorState, Field, InlineError, Input, LoadingState } from '@/shared/ui'
import {
  MIN_PASSWORD_LENGTH,
  RESET_LINK_INVALID_HINT,
  RESET_LINK_INVALID_TITLE,
  passwordProblem,
  resetFailure,
} from '@/features/auth/resetPassword'

interface ResetPreview {
  full_name: string
  expires_at: string
}

export function ResetPasswordPage() {
  const { token = '' } = useParams<{ token: string }>()
  const { me, logout } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  // Ссылка могла умереть уже после проверки (её использовали в другой вкладке).
  const [dead, setDead] = useState(false)
  // Пароль сохранён, идёт переход в систему: показывать в этот момент «Вы уже вошли» нельзя.
  const [done, setDone] = useState(false)

  const preview = useQuery({
    queryKey: ['reset-preview', token],
    queryFn: ({ signal }) =>
      api.get<ResetPreview>(`/auth/reset-password/${encodeURIComponent(token)}`, undefined, signal),
    retry: false,
    // Ссылка не должна пережить страницу в кэше: она одноразовая.
    gcTime: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const problem = passwordProblem(password, repeat)
    if (problem) {
      setError(problem)
      return
    }
    setPending(true)
    try {
      await api.post('/auth/reset-password', { token, password })
      setDone(true)
      // Сервер уже поставил сессию в cookie. Дожидаемся профиля, и только потом
      // уходим с экрана: иначе защищённый маршрут на миг отправит на страницу входа.
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      navigate('/chats', { replace: true })
    } catch (cause) {
      const failure = resetFailure(cause)
      if (failure === 'invalid') setDead(true)
      else setError(cause instanceof ApiError ? cause.message : 'Не удалось сохранить пароль')
    } finally {
      setPending(false)
    }
  }

  const linkFailure = dead ? 'invalid' : preview.error ? resetFailure(preview.error) : null

  async function leaveAndContinue() {
    await logout()
    // Выход чистит кэш разделов вместе с проверкой ссылки — спрашиваем заново.
    void preview.refetch()
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-5 py-8">
      <div className="flex w-full max-w-sm flex-col gap-6">
        {done ? (
          <LoadingState className="py-8" />
        ) : me ? (
          // Ссылка предназначена тому, кто забыл пароль. Вошедшему в другую учётную
          // запись её молча применять нельзя: вход подменил бы его сессию.
          <div className="flex flex-col items-center gap-4 text-center">
            <h1 className="text-2xl font-semibold text-ink">Вы уже вошли в систему</h1>
            <p className="text-sm text-ink-muted">
              Вы вошли как {me.full_name}. Эта ссылка нужна сотруднику, который забыл пароль. Чтобы
              задать пароль по ссылке, сначала выйдите из системы.
            </p>
            <div className="flex w-full flex-col gap-2">
              <Button type="button" size="lg" fullWidth onClick={() => void leaveAndContinue()}>
                Выйти и продолжить
              </Button>
              <Link
                to="/chats"
                className="text-sm text-accent-text underline-offset-4 hover:underline"
              >
                Вернуться к работе
              </Link>
            </div>
          </div>
        ) : linkFailure === 'invalid' ? (
          <div className="flex flex-col items-center gap-4">
            <EmptyState
              title={RESET_LINK_INVALID_TITLE}
              hint={RESET_LINK_INVALID_HINT}
              className="flex-none py-4"
            />
            <Link to="/login" className="text-sm text-accent-text underline-offset-4 hover:underline">
              К странице входа
            </Link>
          </div>
        ) : linkFailure === 'throttled' ? (
          <EmptyState
            title="Слишком много попыток"
            hint={
              preview.error instanceof ApiError
                ? preview.error.message
                : 'Попробуйте немного позже.'
            }
            className="flex-none py-4"
          />
        ) : preview.error ? (
          <ErrorState error={preview.error} onRetry={() => void preview.refetch()} />
        ) : !preview.data ? (
          <LoadingState className="py-8" />
        ) : (
          <>
            <header className="flex flex-col items-center gap-2 text-center">
              <span className="text-3xl" aria-hidden>
                🌙
              </span>
              <h1 className="text-2xl font-semibold text-ink">Задайте новый пароль</h1>
              <p className="text-sm text-ink-muted">
                Здравствуйте, {preview.data.full_name}. После сохранения вы сразу войдёте в систему;
                старый пароль перестанет работать, а на других устройствах вход будет завершён.
              </p>
            </header>

            <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-label="Новый пароль">
              <Field label="Новый пароль" hint={`Не короче ${MIN_PASSWORD_LENGTH} символов`}>
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
                  invalid={repeat.length > 0 && repeat !== password}
                />
              </Field>

              {error && <InlineError message={error} />}

              <Button type="submit" size="lg" fullWidth loading={pending}>
                Сохранить и войти
              </Button>
            </form>

            <p className="text-center text-xs text-ink-faint">
              Ссылка действует до {dateTimeFull(preview.data.expires_at)} и работает один раз.
            </p>
          </>
        )}
      </div>
    </div>
  )
}
