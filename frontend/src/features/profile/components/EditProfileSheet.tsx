/**
 * Смена своего имени и телефона. Почту и роль менеджер себе не меняет —
 * почта это логин (руководитель мог бы её потерять из виду), роль назначает
 * только руководитель.
 */

import { useEffect, useState, type FormEvent } from 'react'

import type { Me } from '@/entities/types'
import { Button, Field, InlineError, Input, Sheet } from '@/shared/ui'
import { errorMessage, fieldErrors } from '@/features/profile/lib'
import { useUpdateMe } from '@/features/profile/queries'

export function EditProfileSheet({
  me,
  open,
  onOpenChange,
}: {
  me: Me
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const update = useUpdateMe()
  const [fullName, setFullName] = useState(me.full_name)
  const [phone, setPhone] = useState(me.phone ?? '')
  const [fields, setFields] = useState<Record<string, string>>({})

  // Значения берутся заново при каждом открытии — свежие из `me`, а не из
  // замыкания на момент рендера кнопки. Иначе после успешного сохранения
  // и повторного открытия в полях мелькали бы старые, дозаписанные значения.
  useEffect(() => {
    if (!open) return
    setFullName(me.full_name)
    setPhone(me.phone ?? '')
    setFields({})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setFields({})
    update.reset()
    if (!fullName.trim()) {
      setFields({ full_name: 'Укажите имя' })
      return
    }
    try {
      await update.mutateAsync({
        full_name: fullName.trim(),
        phone: phone.trim() || null,
      })
      onOpenChange(false)
    } catch (cause) {
      setFields(fieldErrors(cause))
    }
  }

  const serverError = update.error && Object.keys(fieldErrors(update.error)).length === 0
    ? errorMessage(update.error)
    : null

  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Изменить профиль"
      footer={
        <Button type="submit" form="edit-profile-form" fullWidth loading={update.isPending}>
          Сохранить
        </Button>
      }
    >
      <form id="edit-profile-form" onSubmit={submit} className="flex flex-col gap-4">
        <Field label="Имя" error={fields.full_name}>
          <Input value={fullName} onChange={(e) => setFullName(e.target.value)} required />
        </Field>
        <Field label="Телефон" error={fields.phone} hint="Необязательно">
          <Input
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+7 900 000-00-00"
          />
        </Field>
        {serverError && <InlineError message={serverError} />}
      </form>
    </Sheet>
  )
}
