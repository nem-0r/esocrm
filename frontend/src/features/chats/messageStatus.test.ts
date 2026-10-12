import { describe, expect, it } from 'vitest'

import { deliveryView } from './messageStatus'

describe('deliveryView', () => {
  it('в очереди — часы «Отправляется»', () => {
    expect(deliveryView('queued')).toEqual({ state: 'sending', label: 'Отправляется' })
  })

  it('отправлено — одна галочка', () => {
    expect(deliveryView('sent')).toEqual({ state: 'sent', label: 'Отправлено' })
  })

  it('прочитано — две галочки', () => {
    expect(deliveryView('read')).toEqual({ state: 'read', label: 'Прочитано' })
  })

  it('ошибка — красный «!» «Не доставлено»', () => {
    expect(deliveryView('failed')).toEqual({ state: 'failed', label: 'Не доставлено' })
  })

  it('статуса нет — значка нет', () => {
    expect(deliveryView(null)).toBeNull()
    expect(deliveryView(undefined)).toBeNull()
  })

  it('незнакомый статус из будущей версии сервера — без значка, а не с неверным', () => {
    expect(deliveryView('delivered' as never)).toBeNull()
  })

  it('исходящее — значок есть (по умолчанию направление исходящее: так зовёт пузырь)', () => {
    expect(deliveryView('sent', 'out')?.state).toBe('sent')
    expect(deliveryView('sent')?.state).toBe('sent')
  })

  it('входящее — значка нет, какой бы статус у него ни был в базе', () => {
    for (const status of ['queued', 'sent', 'read', 'failed'] as const) {
      expect(deliveryView(status, 'in')).toBeNull()
    }
  })

  it('превью пустого чата (направление неизвестно) — значка нет', () => {
    expect(deliveryView('read', null)).toBeNull()
  })

  it('у каждого состояния своя подпись, и она по-русски', () => {
    const labels = (['queued', 'sent', 'read', 'failed'] as const).map(
      (status) => deliveryView(status)?.label,
    )
    expect(new Set(labels).size).toBe(4)
    for (const label of labels) expect(label).toMatch(/^[А-Яа-яЁё ]+$/)
  })
})
