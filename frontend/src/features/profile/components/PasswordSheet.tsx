/**
 * Смена пароля. Совпадение и длину проверяем на месте, всё остальное —
 * дело сервера, его текст показываем как есть.
 */

import { useState, type FormEvent } from 'react'

import { Button, Field, InlineError, Input, Sheet } from '@/shared/ui'
import { errorMessage } from '@/features/profile/lib'
import { useUpdateMe } from '@/features/profile/queries'

const MIN_LENGTH = 8

export function PasswordSheet({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const update = useUpdateMe()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  function close(value: boolean) {
    onOpenChange(value)
    if (!value) {
      setCurrent('')
      setNext('')
      setRepeat('')
      setLocalError(null)
      setDone(false)
      update.reset()
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setLocalError(null)
    update.reset()
    if (next.length < MIN_LENGTH) {
      setLocalError(`Новый пароль должен быть не короче ${MIN_LENGTH} символов`)
      return
    }
    if (next !== repeat) {
      setLocalError('Пароли не совпадают')
      return
    }
    try {
      await update.mutateAsync({ current_password: current, new_password: next })
      setDone(true)
      setCurrent('')
      setNext('')
      setRepeat('')
    } catch {
      // Текст ошибки показывается ниже, из состояния мутации.
    }
  }

  const serverError = update.error ? errorMessage(update.error) : null

  return (
    <Sheet
      open={open}
      onOpenChange={close}
      title="Сменить пароль"
      description="Другие устройства выйдут из системы, текущее останется в работе."
      footer={
        done ? (
          <Button fullWidth onClick={() => close(false)}>
            Готово
          </Button>
        ) : (
          <Button type="submit" form="password-form" fullWidth loading={update.isPending}>
            Сохранить пароль
          </Button>
        )
      }
    >
      {done ? (
        <p className="rounded border border-success/40 bg-success-soft px-3 py-2 text-sm text-success">
          Пароль изменён.
        </p>
      ) : (
        <form id="password-form" onSubmit={submit} className="flex flex-col gap-4">
          <Field label="Текущий пароль">
            <Input
              type="password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>
          <Field label="Новый пароль" hint={`Не короче ${MIN_LENGTH} символов`}>
            <Input
              type="password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              autoComplete="new-password"
              required
            />
          </Field>
          <Field label="Повторите новый пароль">
            <Input
              type="password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              autoComplete="new-password"
              required
              invalid={repeat.length > 0 && repeat !== next}
            />
          </Field>

          {localError && <InlineError message={localError} />}
          {serverError && <InlineError message={serverError} />}
        </form>
      )}
    </Sheet>
  )
}
