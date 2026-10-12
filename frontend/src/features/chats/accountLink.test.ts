import { describe, expect, it } from 'vitest'

import { OFFLINE_PLACEHOLDER, hasNoLink, offlineBannerText } from './accountLink'

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
})

describe('поле ввода без связи', () => {
  it('пояснение в поле объясняет, почему писать нельзя', () => {
    expect(OFFLINE_PLACEHOLDER).toBe('Аккаунт отключён')
  })
})
