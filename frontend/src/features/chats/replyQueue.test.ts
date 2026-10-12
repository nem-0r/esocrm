import { describe, expect, it } from 'vitest'

import {
  MAX_SEARCH,
  MAX_TAKEN,
  chatPath,
  clearTaken,
  isChatFilter,
  loadFilter,
  loadTaken,
  overFilterLabel,
  parseTaken,
  pruneTaken,
  pushTaken,
  queueDoneText,
  saveFilter,
  saveTaken,
  searchFromParams,
  skipParam,
  takenIds,
  withSearch,
  type StorageLike,
  type TakenEntry,
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

const entry = (id: number, since: string | null = null): TakenEntry => ({ id, since })
const ids = (taken: TakenEntry[]) => taken.map((item) => item.id)

describe('взятые в сессии чаты', () => {
  it('новый чат добавляется в конец', () => {
    expect(ids(pushTaken([entry(4), entry(9)], entry(7)))).toEqual([4, 9, 7])
  })

  it('повторно взятый не дублируется, становится последним и получает новое время ожидания', () => {
    const taken = pushTaken([entry(4), entry(9, 'старое'), entry(7)], entry(9, 'новое'))
    expect(taken).toEqual([entry(4), entry(7), entry(9, 'новое')])
  })

  it('хранится не больше MAX_TAKEN последних — столько принимает сервер', () => {
    let taken: TakenEntry[] = []
    for (let id = 1; id <= MAX_TAKEN + 20; id++) taken = pushTaken(taken, entry(id))
    expect(taken).toHaveLength(MAX_TAKEN)
    expect(taken[0].id).toBe(21)
    expect(taken[taken.length - 1].id).toBe(MAX_TAKEN + 20)
  })

  it('параметр skip — номера через запятую, пустой список — без параметра', () => {
    expect(skipParam(takenIds([entry(5), entry(3), entry(9)]))).toBe('5,3,9')
    expect(skipParam([])).toBeUndefined()
  })

  it('чужой мусор в хранилище не ломает кнопку', () => {
    expect(parseTaken(null)).toEqual([])
    expect(parseTaken('не json')).toEqual([])
    expect(parseTaken('{"a":1}')).toEqual([])
    expect(ids(parseTaken('[1,"x",-3,0,2.5,7,7,{"id":"8"},{"id":9,"since":5},null]'))).toEqual([1, 7, 9])
  })

  it('слишком длинный список из хранилища обрезается до последних', () => {
    const raw = JSON.stringify(Array.from({ length: 120 }, (_, i) => entry(i + 1)))
    const parsed = parseTaken(raw)
    expect(parsed).toHaveLength(MAX_TAKEN)
    expect(parsed[parsed.length - 1].id).toBe(120)
  })

  it('прежний формат (голые номера) читается как «не ждал»: вкладка не теряет список', () => {
    expect(parseTaken('[4,9]')).toEqual([entry(4), entry(9)])
  })

  it('повтор номера в хранилище: побеждает последняя запись, порядок сохраняется', () => {
    const raw = JSON.stringify([entry(4, 'a'), entry(9), entry(4, 'b')])
    expect(parseTaken(raw)).toEqual([entry(9), entry(4, 'b')])
  })
})

describe('чистка взятых: кто написал снова, тот снова в очереди', () => {
  const T1 = '2026-10-11T09:00:00Z'
  const T2 = '2026-10-11T12:00:00Z'

  it('ждёт с тех же пор — «уже открывал», остаётся пропущенным', () => {
    const taken = [entry(1, T1)]
    expect(pruneTaken(taken, [{ id: 1, awaiting_reply_since: T1 }])).toEqual(taken)
  })

  it('ответили, клиент написал снова (ожидание новее момента взятия) — убирается из пропущенных', () => {
    const taken = [entry(1, T1), entry(2, T1)]
    const left = pruneTaken(taken, [
      { id: 1, awaiting_reply_since: T2 },
      { id: 2, awaiting_reply_since: T1 },
    ])
    expect(ids(left)).toEqual([2])
  })

  it('чат не ждал, когда его брали («самый свежий»), а теперь ждёт, — убирается', () => {
    expect(ids(pruneTaken([entry(5, null)], [{ id: 5, awaiting_reply_since: T2 }]))).toEqual([])
  })

  it('сейчас не ждёт — остаётся: когда ждущих нет, «Взять первого» не должен возвращаться к нему', () => {
    expect(pruneTaken([entry(1, T1)], [])).toEqual([entry(1, T1)])
    expect(pruneTaken([entry(1, T1)], [{ id: 1, awaiting_reply_since: null }])).toEqual([entry(1, T1)])
  })

  it('чужие ждущие чаты на взятых не влияют', () => {
    expect(pruneTaken([entry(1, T1)], [{ id: 99, awaiting_reply_since: T2 }])).toEqual([entry(1, T1)])
  })

  it('время не читается — не трогаем: хуже потерять «уже открывал», чем показать дважды', () => {
    expect(pruneTaken([entry(1, 'вчера')], [{ id: 1, awaiting_reply_since: T2 }])).toHaveLength(1)
  })

  it('без взятых и без ждущих — пусто, без ошибок', () => {
    expect(pruneTaken([], [])).toEqual([])
  })
})

describe('текст при «очередь пройдена»', () => {
  it('ждущие есть — все они уже открыты', () => {
    expect(queueDoneText(3)).toBe('Все ждущие чаты вы уже открывали. Нажмите ещё раз — начнём сначала')
  })

  it('ждущих нет — перебраны все чаты', () => {
    expect(queueDoneText(0)).toBe('Больше нет чатов в очереди. Нажмите ещё раз — начнём сначала')
  })
})

describe('текст поиска в адресе', () => {
  it('читается из ?q= с обрезкой пробелов; нет параметра — пусто', () => {
    expect(searchFromParams(new URLSearchParams('q=%20Ксения%20'))).toBe('Ксения')
    expect(searchFromParams(new URLSearchParams(''))).toBe('')
  })

  it('руками вписанный сверхдлинный текст обрезается до предела сервера', () => {
    const long = 'я'.repeat(MAX_SEARCH + 50)
    expect(searchFromParams(new URLSearchParams({ q: long }))).toHaveLength(MAX_SEARCH)
  })

  it('запись нового текста не трогает остальные параметры; пустой — убирает ?q=', () => {
    const base = new URLSearchParams('filter=x&q=старый')
    expect(withSearch(base, 'новый').toString()).toBe('filter=x&q=%D0%BD%D0%BE%D0%B2%D1%8B%D0%B9')
    expect(withSearch(base, '   ').toString()).toBe('filter=x')
  })

  it('адрес чата несёт поиск: список слева не сбрасывается', () => {
    expect(chatPath(12, '')).toBe('/chats/12')
    expect(chatPath(12, 'Анна Иванова')).toBe('/chats/12?q=%D0%90%D0%BD%D0%BD%D0%B0+%D0%98%D0%B2%D0%B0%D0%BD%D0%BE%D0%B2%D0%B0')
    expect(new URLSearchParams(chatPath(12, '@anna&co').split('?')[1]).get('q')).toBe('@anna&co')
  })
})

describe('хранилище сессии', () => {
  it('взятые чаты сохраняются отдельно для каждого сотрудника', () => {
    const storage = fakeStorage()
    saveTaken(1, [entry(10, 'a'), entry(11)], storage)
    saveTaken(2, [entry(20)], storage)
    expect(loadTaken(1, storage)).toEqual([entry(10, 'a'), entry(11)])
    expect(loadTaken(2, storage)).toEqual([entry(20)])
  })

  it('круг заново: список очищается', () => {
    const storage = fakeStorage()
    saveTaken(1, [entry(10), entry(11)], storage)
    clearTaken(1, storage)
    expect(loadTaken(1, storage)).toEqual([])
  })

  it('хранилище недоступно — работаем из памяти страницы, ничего не падает', () => {
    const broken = fakeStorage(true)
    expect(() => saveTaken(77, [entry(1), entry(2), entry(3)], broken)).not.toThrow()
    expect(ids(loadTaken(77, broken))).toEqual([1, 2, 3])
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
