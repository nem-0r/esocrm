import { describe, expect, it } from 'vitest'

import {
  acceptingLeadsHint,
  accountAttentionBanner,
  accountsWithoutLink,
  attentionFilterOn,
  isWithoutAccount,
  notificationTarget,
  withAttentionFilter,
} from './rules'

describe('notificationTarget: куда ведёт строка уведомления', () => {
  it('оплата ведёт в карточку сделки — и руководителя, и менеджера', () => {
    const deal = { entity_type: 'deal', entity_id: 4 }
    expect(notificationTarget(deal, true)).toBe('/payments/4')
    expect(notificationTarget(deal, false)).toBe('/payments/4')
  })

  it('аккаунт у руководителя ведёт в «Аккаунты»', () => {
    expect(notificationTarget({ entity_type: 'account', entity_id: 9 }, true)).toBe(
      '/profile/accounts',
    )
  })

  it('аккаунт у менеджера ведёт в «Профиль»: «Аккаунты» ему закрыты', () => {
    expect(notificationTarget({ entity_type: 'account', entity_id: 9 }, false)).toBe('/profile')
  })

  it('неизвестный вид объекта — строка не кликабельна', () => {
    expect(notificationTarget({ entity_type: 'client', entity_id: 1 }, true)).toBeNull()
    expect(notificationTarget({ entity_type: null, entity_id: null }, true)).toBeNull()
  })

  it('без номера объекта вести некуда', () => {
    expect(notificationTarget({ entity_type: 'deal', entity_id: null }, true)).toBeNull()
    expect(notificationTarget({ entity_type: 'account', entity_id: null }, true)).toBeNull()
  })

  it('мусорный номер не превращается в адрес', () => {
    expect(notificationTarget({ entity_type: 'deal', entity_id: 0 }, true)).toBeNull()
    expect(notificationTarget({ entity_type: 'deal', entity_id: -3 }, true)).toBeNull()
    expect(notificationTarget({ entity_type: 'deal', entity_id: 1.5 }, true)).toBeNull()
    expect(notificationTarget({ entity_type: 'deal', entity_id: Number.NaN }, true)).toBeNull()
  })
})

describe('accountAttentionBanner: плашка «аккаунт требует внимания»', () => {
  it('у руководителя плашка — ссылка в список с включённым фильтром', () => {
    expect(accountAttentionBanner(1, true)).toEqual({
      text: '1 аккаунт требует внимания',
      href: '/profile/accounts?attention=1',
    })
    expect(accountAttentionBanner(3, true).text).toBe('3 аккаунта требуют внимания')
    expect(accountAttentionBanner(5, true).text).toBe('5 аккаунтов требуют внимания')
  })

  it('у менеджера плашка без ссылки: раздел «Аккаунты» ему закрыт', () => {
    expect(accountAttentionBanner(1, false)).toEqual({
      text: 'Аккаунт отключён, сообщите руководителю',
      href: null,
    })
  })

  it('у менеджера с несколькими аккаунтами — число и склонение', () => {
    expect(accountAttentionBanner(2, false).text).toBe('2 аккаунта отключены, сообщите руководителю')
    expect(accountAttentionBanner(5, false).text).toBe(
      '5 аккаунтов отключены, сообщите руководителю',
    )
    expect(accountAttentionBanner(2, false).href).toBeNull()
  })
})

describe('accountsWithoutLink: сколько аккаунтов менеджера не работают', () => {
  it('считает всё, что не подключено: нет связи, ждёт входа, отключён', () => {
    expect(
      accountsWithoutLink([
        { status: 'connected' },
        { status: 'error' },
        { status: 'pending' },
        { status: 'disconnected' },
      ]),
    ).toBe(3)
  })

  it('все подключены — ноль, плашки нет', () => {
    expect(accountsWithoutLink([{ status: 'connected' }, { status: 'connected' }])).toBe(0)
    expect(accountsWithoutLink([])).toBe(0)
  })
})

describe('фильтр «требуют внимания» в адресе страницы «Аккаунты»', () => {
  it('?attention=1 включает фильтр', () => {
    expect(attentionFilterOn(new URLSearchParams('attention=1'))).toBe(true)
  })

  it('без параметра и с любым другим значением фильтр выключен', () => {
    expect(attentionFilterOn(new URLSearchParams(''))).toBe(false)
    expect(attentionFilterOn(new URLSearchParams('attention=0'))).toBe(false)
    expect(attentionFilterOn(new URLSearchParams('attention='))).toBe(false)
  })

  it('включение и снятие меняют только свой параметр', () => {
    const on = withAttentionFilter(new URLSearchParams('x=1'), true)
    expect(on.get('attention')).toBe('1')
    expect(on.get('x')).toBe('1')
    const off = withAttentionFilter(on, false)
    expect(off.has('attention')).toBe(false)
    expect(off.get('x')).toBe('1')
  })

  it('снять фильтр, которого нет, — не ошибка', () => {
    expect(withAttentionFilter(new URLSearchParams(''), false).toString()).toBe('')
  })
})

describe('isWithoutAccount: плашка «· без аккаунта» и фильтр на сервере совпадают', () => {
  const none: { id: number; title: string; funnel_stage: 'sales' }[] = []
  const one = [{ id: 1, title: 'Марина', funnel_stage: 'sales' as const }]

  it('активный менеджер без аккаунтов — без аккаунта', () => {
    expect(isWithoutAccount({ role: 'manager', is_active: true, accounts: none })).toBe(true)
  })

  it('активный менеджер с аккаунтом — нет', () => {
    expect(isWithoutAccount({ role: 'manager', is_active: true, accounts: one })).toBe(false)
  })

  it('руководителю аккаунт не нужен', () => {
    expect(isWithoutAccount({ role: 'admin', is_active: true, accounts: none })).toBe(false)
  })

  it('отключённого сотрудника не считаем', () => {
    expect(isWithoutAccount({ role: 'manager', is_active: false, accounts: none })).toBe(false)
  })
})

describe('acceptingLeadsHint: подпись под переключателем «Приём заявок»', () => {
  const me = 7
  const solo = { managers: [{ id: me }] }
  const shared = { managers: [{ id: me }, { id: 8 }] }
  const foreign = { managers: [{ id: 8 }] }

  it('включён и есть аккаунт, где менеджер один: говорит, что диалоги закрепляются', () => {
    const text = acceptingLeadsHint(true, [solo, shared], me)
    expect(text).toContain('закрепляются за вами')
    expect(text).toContain('работаете один')
  })

  it('выключен и есть аккаунт, где менеджер один: говорит, что не закрепляются', () => {
    const text = acceptingLeadsHint(false, [solo], me)
    expect(text).toContain('не закрепляются')
    expect(text).toContain('работаете один')
  })

  it('подписи включённого и выключенного состояний различаются', () => {
    expect(acceptingLeadsHint(true, [solo], me)).not.toBe(acceptingLeadsHint(false, [solo], me))
  })

  it('выключенный не обещает того, чего нет: «не назначаются» без оговорок — запрещено', () => {
    expect(acceptingLeadsHint(false, [solo], me)).not.toBe(
      'Пока выключен, новые диалоги вам не назначаются',
    )
  })

  it('только общие аккаунты: честно говорит, что переключатель пока ничего не меняет', () => {
    for (const enabled of [true, false]) {
      const text = acceptingLeadsHint(enabled, [shared, shared], me)
      expect(text).toContain('ничего не меняет')
      expect(text).toContain('несколько менеджеров')
    }
  })

  it('чужие аккаунты не считаются «моими»', () => {
    const text = acceptingLeadsHint(true, [foreign], me)
    expect(text).toContain('не назначены')
  })

  it('нет аккаунтов: переключатель ни на что не влияет', () => {
    for (const enabled of [true, false]) {
      expect(acceptingLeadsHint(enabled, [], me)).toContain('не назначены')
    }
  })

  it('аккаунты ещё не загрузились: общая формулировка, верная в любом случае', () => {
    const on = acceptingLeadsHint(true, null, me)
    const off = acceptingLeadsHint(false, null, me)
    expect(on).toContain('единственный менеджер')
    expect(off).toContain('единственный менеджер')
    expect(on).not.toBe(off)
  })
})
