import { describe, expect, it } from 'vitest'

import { decidePaste, nameForPasted } from './clipboard'

function transfer(files: File[], text = '') {
  return { files, getData: (format: string) => (format === 'text/plain' ? text : '') }
}

const png = (name = 'image.png') => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' })
const pdf = (name = 'договор.pdf') =>
  new File([new Uint8Array([1])], name, { type: 'application/pdf' })

describe('вставка из буфера', () => {
  it('скриншот прикрепляется', () => {
    expect(decidePaste(transfer([png()])).files).toHaveLength(1)
  })

  it('обычный текст вставляется как текст', () => {
    expect(decidePaste(transfer([], 'Здравствуйте')).files).toHaveLength(0)
  })

  it('ячейки Excel (текст + снимок) — вставляется текст', () => {
    expect(decidePaste(transfer([png()], 'Имя\tСумма\nАнна\t4900')).files).toHaveLength(0)
  })

  it('файл из Finder (файл + его имя текстом) — прикрепляется файл', () => {
    const file = png('Фото клиента.png')
    expect(decidePaste(transfer([file], 'Фото клиента.png')).files).toEqual([file])
  })

  it('не-картинка всегда файл, даже с текстом', () => {
    const file = pdf()
    expect(decidePaste(transfer([file], 'что-то ещё')).files).toEqual([file])
  })

  it('пустые файлы не прикрепляются', () => {
    const empty = new File([], 'пусто.png', { type: 'image/png' })
    expect(decidePaste(transfer([empty])).files).toHaveLength(0)
  })

  it('нет данных — ничего не делаем', () => {
    expect(decidePaste(null).files).toHaveLength(0)
  })
})

describe('имя вставленного скриншота', () => {
  it('безликое image.png получает дату и время', () => {
    const name = nameForPasted(png(), new Date(2026, 8, 27, 14, 5, 9))
    expect(name).toBe('Скриншот 2026-09-27 14-05-09.png')
  })

  it('нормальное имя не трогаем', () => {
    expect(nameForPasted(png('Карта Анны.png'))).toBe('Карта Анны.png')
  })
})
