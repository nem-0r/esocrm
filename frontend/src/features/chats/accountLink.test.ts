import { describe, expect, it } from 'vitest'

import { ApiError } from '@/shared/api/client'

import {
  ACCOUNT_OFFLINE,
  OFFLINE_PLACEHOLDER,
  RECONNECT_HREF,
  blockedAttachText,
  blockedPlaceholder,
  canReconnect,
  hasNoLink,
  isAccountDeleted,
  isAccountOffline,
  offlineBannerText,
  offlineReason,
} from './accountLink'

describe('hasNoLink', () => {
  it('отправка закрыта при error, disconnected и pending — как на сервере', () => {
    expect(hasNoLink('error')).toBe(true)
    expect(hasNoLink('disconnected')).toBe(true)
    expect(hasNoLink('pending')).toBe(true)
  })

  it('подключённый аккаунт пишет', () => {
    expect(hasNoLink('connected')).toBe(false)
  })

  it('старый сервер статус не присылает — не выдумываем обрыв', () => {
    expect(hasNoLink(undefined)).toBe(false)
    expect(hasNoLink(null)).toBe(false)
  })
})

describe('offlineBannerText', () => {
  it('менеджеру: название аккаунта, что сообщения не уходят и кому сообщить', () => {
    expect(offlineBannerText('Марина · допы', false)).toBe(
      'Аккаунт «Марина · допы» отключён — сообщения не уходят. Сообщите руководителю',
    )
  })

  it('руководителю не предлагаем сообщить самому себе', () => {
    const text = offlineBannerText('Марина · допы', true)
    expect(text).toBe('Аккаунт «Марина · допы» отключён — сообщения не уходят.')
    expect(text).not.toContain('Сообщите руководителю')
  })

  it('удалённый аккаунт назван удалённым, а не «отключённым»', () => {
    expect(offlineBannerText('Марина · допы', true, 'disconnected')).toBe(
      'Аккаунт «Марина · допы» удалён — сообщения не уходят.',
    )
    expect(offlineBannerText('Марина · допы', false, 'disconnected')).toBe(
      'Аккаунт «Марина · допы» удалён — сообщения не уходят. Сообщите руководителю',
    )
  })

  it('вход не завершён — «не подключён»; потеря сессии — «отключён»', () => {
    expect(offlineBannerText('A', true, 'pending')).toContain('не подключён')
    expect(offlineBannerText('A', true, 'error')).toContain('отключён')
  })
})

describe('«Переподключить» у руководителя', () => {
  it('есть для потерянной сессии и незавершённого входа', () => {
    expect(canReconnect('error', true)).toBe(true)
    expect(canReconnect('pending', true)).toBe(true)
  })

  it('нет для удалённого аккаунта: в списке «Аккаунты» такой записи нет', () => {
    expect(isAccountDeleted('disconnected')).toBe(true)
    expect(canReconnect('disconnected', true)).toBe(false)
  })

  it('нет у менеджера и у рабочего аккаунта', () => {
    expect(canReconnect('error', false)).toBe(false)
    expect(canReconnect('connected', true)).toBe(false)
    expect(canReconnect(undefined, true)).toBe(false)
  })

  it('ведёт в «Аккаунты» с включённым фильтром «требуют внимания»', () => {
    expect(RECONNECT_HREF).toBe('/profile/accounts?attention=1')
  })
})

describe('поле ввода без связи', () => {
  it('пояснение в поле объясняет, почему писать нельзя', () => {
    expect(OFFLINE_PLACEHOLDER).toBe('Аккаунт отключён')
    expect(offlineReason('error')).toBe('Аккаунт отключён')
    expect(offlineReason('disconnected')).toBe('Аккаунт удалён')
    expect(offlineReason(undefined)).toBe('Аккаунт отключён')
  })

  it('подсказывает, что заметку написать можно', () => {
    expect(blockedPlaceholder('Аккаунт отключён')).toBe('Аккаунт отключён — только заметки')
  })

  it('брошенный файл не теряется молча: текст причины для уведомления', () => {
    expect(blockedAttachText('Аккаунт отключён')).toBe(
      'Файл не прикреплён: аккаунт отключён, клиенту сейчас ничего не отправить',
    )
  })
})

describe('isAccountOffline', () => {
  it('узнаёт отказ по коду 409, а не по тексту', () => {
    expect(isAccountOffline(new ApiError(409, ACCOUNT_OFFLINE, 'любой текст'))).toBe(true)
  })

  it('прочие конфликты и ошибки — нет', () => {
    expect(isAccountOffline(new ApiError(409, 'conflict', 'Аккаунт без связи'))).toBe(false)
    expect(isAccountOffline(new ApiError(404, ACCOUNT_OFFLINE, 'x'))).toBe(false)
    expect(isAccountOffline(new Error('account_offline'))).toBe(false)
    expect(isAccountOffline(null)).toBe(false)
  })
})
