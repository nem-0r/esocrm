import { describe, expect, it } from 'vitest'

import {
  MAX_TAKEN,
  isChatFilter,
  loadFilter,
  loadTaken,
  overFilterLabel,
  parseTaken,
  pushTaken,
  saveFilter,
  saveTaken,
  clearTaken,
  skipParam,
  type StorageLike,
} from './replyQueue'

function fakeStorage(broken = false): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>()
  const fail = () => {
    if (broken) throw new Error('storage disabled')
  }
  return {
    data,
    getItem: (key) => {
      fail()
      return data.get(key) ?? null
    },
    setItem: (key, value) => {
      fail()
      data.set(key, value)
    },
    removeItem: (key) => {
      fail()
      data.delete(key)
    },
  }
}

describe('взятые в сессии чаты', () => {
  it('новый чат добавляется в конец', () => {
    expect(pushTaken([4, 9], 7)).toEqual([4, 9, 7])
  })

  it('повторно взятый не дублируется и становится последним', () => {
    expect(pushTaken([4, 9, 7], 9)).toEqual([4, 7, 9])
  })

  it('хранится не больше MAX_TAKEN последних — столько принимает сервер', () => {
    let taken: number[] = []
    for (let id = 1; id <= MAX_TAKEN + 20; id++) taken = pushTaken(taken, id)
    expect(taken).toHaveLength(MAX_TAKEN)
    expect(taken[0]).toBe(21)
    expect(taken[taken.length - 1]).toBe(MAX_TAKEN + 20)
  })

  it('параметр skip — номера через запятую, пустой список — без параметра', () => {
    expect(skipParam([5, 3, 9])).toBe('5,3,9')
    expect(skipParam([])).toBeUndefined()
  })

  it('чужой мусор в хранилище не ломает кнопку', () => {
    expect(parseTaken(null)).toEqual([])
    expect(parseTaken('не json')).toEqual([])
    expect(parseTaken('{"a":1}')).toEqual([])
    expect(parseTaken('[1,"x",-3,0,2.5,7,7]')).toEqual([1, 7])
  })

  it('слишком длинный список из хранилища обрезается до последних', () => {
    const raw = JSON.stringify(Array.from({ length: 120 }, (_, i) => i + 1))
    const parsed = parseTaken(raw)
    expect(parsed).toHaveLength(MAX_TAKEN)
    expect(parsed[parsed.length - 1]).toBe(120)
  })
})

describe('хранилище сессии', () => {
  it('взятые чаты сохраняются отдельно для каждого сотрудника', () => {
    const storage = fakeStorage()
    saveTaken(1, [10, 11], storage)
    saveTaken(2, [20], storage)
    expect(loadTaken(1, storage)).toEqual([10, 11])
    expect(loadTaken(2, storage)).toEqual([20])
  })

  it('круг заново: список очищается', () => {
    const storage = fakeStorage()
    saveTaken(1, [10, 11], storage)
    clearTaken(1, storage)
    expect(loadTaken(1, storage)).toEqual([])
  })

  it('хранилище недоступно — работаем из памяти страницы, ничего не падает', () => {
    const broken = fakeStorage(true)
    expect(() => saveTaken(77, [1, 2, 3], broken)).not.toThrow()
    expect(loadTaken(77, broken)).toEqual([1, 2, 3])
    expect(() => clearTaken(77, broken)).not.toThrow()
    expect(loadTaken(77, broken)).toEqual([])
  })

  it('выбранный фильтр списка переживает пересборку раздела', () => {
    const storage = fakeStorage()
    expect(loadFilter(storage)).toBe('all')
    saveFilter('awaiting_over', storage)
    expect(loadFilter(storage)).toBe('awaiting_over')
  })

  it('неизвестный фильтр в хранилище — «Все»', () => {
    const storage = fakeStorage()
    storage.setItem('crm.chats.filter', 'что-то-старое')
    expect(loadFilter(storage)).toBe('all')
  })

  it('фильтр без хранилища тоже запоминается в памяти страницы', () => {
    const broken = fakeStorage(true)
    saveFilter('awaiting', broken)
    expect(loadFilter(broken)).toBe('awaiting')
    saveFilter('all', broken)
  })
})

describe('фильтр «Ждут дольше порога»', () => {
  it('подпись берёт число минут из настройки', () => {
    expect(overFilterLabel(30)).toBe('Ждут >30 мин')
    expect(overFilterLabel(45)).toBe('Ждут >45 мин')
  })

  it('пока порог не загружен — подпись без числа, а не зашитое «30»', () => {
    expect(overFilterLabel(undefined)).toBe('Ждут дольше порога')
    expect(overFilterLabel(0)).toBe('Ждут дольше порога')
  })

  it('значение фильтра проверяется по списку известных', () => {
    expect(isChatFilter('awaiting_over')).toBe(true)
    expect(isChatFilter('all')).toBe(true)
    expect(isChatFilter('over')).toBe(false)
    expect(isChatFilter(null)).toBe(false)
  })
})
