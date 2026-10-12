// @vitest-environment jsdom
/**
 * Страница «Новый пароль» по ссылке руководителя (D-38) и подсказка на странице входа.
 *
 * Библиотеки тестирования компонентов в проекте нет: страницы рисуются настоящим
 * React в jsdom, сервер подменён. Проверяем то, что видит человек, и какие
 * запросы уходят — в частности, что некорректный ввод не доходит до сервера.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const auth = vi.hoisted(() => ({
  me: null as { full_name: string } | null,
  logout: vi.fn(async () => undefined),
}))

vi.mock('@/shared/hooks/useAuth', () => ({
  useAuth: () => ({
    me: auth.me,
    isLoading: false,
    error: null,
    isAdmin: false,
    login: vi.fn(),
    logout: auth.logout,
    refresh: vi.fn(),
  }),
}))

import { LoginPage } from './LoginPage'
import { ResetPasswordPage } from './ResetPasswordPage'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

interface Call {
  method: string
  path: string
  body: unknown
}

type Reply = { status: number; body: unknown }

const TOKEN = 'Tok_en-123'
// Токен проверяется в теле запроса, а не в адресе: адреса оседают в журналах сервера.
const CHECK_PATH = '/api/v1/auth/reset-password/check'
const SUBMIT_PATH = '/api/v1/auth/reset-password'
const LIVE: Reply = {
  status: 200,
  body: { full_name: 'Мария Тестова', expires_at: '2026-10-13T12:00:00Z' },
}
const DEAD: Reply = {
  status: 404,
  body: {
    error: {
      code: 'reset_link_invalid',
      message: 'Ссылка недействительна или истекла. Попросите руководителя выдать новую',
      details: {},
    },
  },
}

let calls: Call[]
let checkReply: Reply
let submitReply: Reply
let root: Root
let host: HTMLElement

function respond({ status, body }: Reply): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  calls = []
  checkReply = LIVE
  submitReply = { status: 200, body: { id: 7, token: 'session' } }
  auth.me = null
  auth.logout.mockClear()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      const url = new URL(input, 'http://localhost')
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ method, path: url.pathname, body })
      if (method === 'POST' && url.pathname === CHECK_PATH) return respond(checkReply)
      if (method === 'POST' && url.pathname === SUBMIT_PATH) return respond(submitReply)
      return respond({ status: 200, body: {} })
    }),
  )
  window.history.replaceState(null, '', '/')
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

async function settle() {
  for (let i = 0; i < 8; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

/**
 * Открыть страницу по адресу `entry`. Адресная строка браузера (window.location)
 * ставится в тот же адрес — так страница видит её в жизни, и по ней проверяем,
 * что токен оттуда стёрт.
 */
async function showReset(entry = `/reset#${TOKEN}`) {
  window.history.replaceState(null, '', entry)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  await act(async () => {
    root.render(
      createElement(
        QueryClientProvider,
        { client: queryClient },
        createElement(
          MemoryRouter,
          { initialEntries: [entry] },
          createElement(
            Routes,
            null,
            createElement(Route, { path: '/reset', element: createElement(ResetPasswordPage) }),
            createElement(Route, { path: '/reset/:token', element: createElement(ResetPasswordPage) }),
            createElement(Route, { path: '/chats', element: createElement('div', null, 'ЧАТЫ') }),
            createElement(Route, { path: '/login', element: createElement('div', null, 'ВХОД') }),
          ),
        ),
      ),
    )
  })
  await settle()
}

const text = () => document.body.textContent ?? ''
const passwordFields = () =>
  Array.from(document.body.querySelectorAll<HTMLInputElement>('input[type="password"]'))
const submits = () => calls.filter((c) => c.method === 'POST' && c.path === SUBMIT_PATH)
const checks = () => calls.filter((c) => c.path === CHECK_PATH)

function byText(selector: string, needle: string): HTMLElement | null {
  const nodes = Array.from(document.body.querySelectorAll<HTMLElement>(selector))
  return nodes.find((node) => node.textContent?.includes(needle)) ?? null
}

async function type(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(input, value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

async function fillAndSubmit(password: string, repeat: string) {
  const [first, second] = passwordFields()
  await type(first, password)
  await type(second, repeat)
  await act(async () => {
    byText('button', 'Сохранить и войти')!.click()
  })
  await settle()
}

describe('страница «Новый пароль»', () => {
  it('живая ссылка: приветствие по имени, два поля пароля, срок и «один раз»', async () => {
    await showReset()
    expect(text()).toContain('Здравствуйте, Мария Тестова')
    expect(passwordFields()).toHaveLength(2)
    expect(passwordFields().every((f) => f.autocomplete === 'new-password')).toBe(true)
    expect(text()).toContain('Не короче 8 символов')
    expect(text()).toContain('работает один раз')
    expect(byText('button', 'Сохранить и войти')).not.toBeNull()
    // Токен ушёл в проверку в теле запроса, и больше ничего лишнего не запрашивалось
    expect(calls).toEqual([{ method: 'POST', path: CHECK_PATH, body: { token: TOKEN } }])
  })

  it('токен сразу убран из адресной строки, в адресах запросов его нет', async () => {
    await showReset()
    expect(window.location.pathname).toBe('/reset')
    expect(window.location.hash).toBe('')
    expect(window.location.href).not.toContain(TOKEN)
    expect(calls.every((c) => !c.path.includes(TOKEN))).toBe(true)
    // Страница при этом работает: токен запомнен, пароль уходит с ним
    await fillAndSubmit('Qa-second-5678', 'Qa-second-5678')
    expect(submits()).toEqual([
      { method: 'POST', path: SUBMIT_PATH, body: { token: TOKEN, password: 'Qa-second-5678' } },
    ])
  })

  it('прежняя ссылка /reset/<токен> тоже работает, и токен из адреса стирается', async () => {
    await showReset(`/reset/${TOKEN}`)
    expect(text()).toContain('Здравствуйте, Мария Тестова')
    expect(checks()).toEqual([{ method: 'POST', path: CHECK_PATH, body: { token: TOKEN } }])
    expect(window.location.pathname).toBe('/reset')
    expect(window.location.href).not.toContain(TOKEN)
  })

  it('/reset без токена: сразу «ссылка недействительна», на сервер ничего не уходит', async () => {
    await showReset('/reset')
    expect(text()).toContain('Ссылка недействительна')
    expect(passwordFields()).toHaveLength(0)
    expect(calls).toHaveLength(0)
  })

  it('пароль длиннее 72 байт (русская фраза): ошибка у формы, запрос не уходит', async () => {
    await showReset()
    expect(text()).toContain('не длиннее 72')
    const phrase = 'Очень длинная парольная фраза для проверки сети'
    await fillAndSubmit(phrase, phrase)
    expect(text()).toContain('Пароль слишком длинный')
    expect(submits()).toHaveLength(0)
  })

  it('недействительная ссылка: объяснение, что делать, и путь ко входу; формы нет', async () => {
    checkReply = DEAD
    await showReset()
    expect(text()).toContain('Ссылка недействительна')
    expect(text()).toContain('Попросите руководителя выдать новую')
    expect(passwordFields()).toHaveLength(0)
    expect(document.body.querySelector('a[href="/login"]')).not.toBeNull()
  })

  it('слишком много неверных попыток: сообщение сервера, формы нет', async () => {
    checkReply = {
      status: 429,
      body: { error: { code: 'rate_limited', message: 'Слишком много неверных попыток. Попробуйте через 15 минут' } },
    }
    await showReset()
    expect(text()).toContain('Слишком много попыток')
    expect(text()).toContain('через 15 минут')
    expect(passwordFields()).toHaveLength(0)
  })

  it('пароли не совпадают: ошибка у формы, запрос на сервер не уходит', async () => {
    await showReset()
    await fillAndSubmit('Qa-second-5678', 'Qa-second-0000')
    expect(text()).toContain('Пароли не совпадают')
    expect(submits()).toHaveLength(0)
  })

  it('короткий пароль: ошибка, запрос не уходит', async () => {
    await showReset()
    await fillAndSubmit('short', 'short')
    expect(text()).toContain('не короче 8')
    expect(submits()).toHaveLength(0)
  })

  it('верный ввод: уходит токен и пароль, затем переход к чатам', async () => {
    await showReset()
    await fillAndSubmit('Qa-second-5678', 'Qa-second-5678')
    expect(submits()).toEqual([
      { method: 'POST', path: SUBMIT_PATH, body: { token: TOKEN, password: 'Qa-second-5678' } },
    ])
    expect(text()).toContain('ЧАТЫ')
  })

  it('ссылку успели использовать в другой вкладке: тот же экран «недействительна»', async () => {
    await showReset()
    submitReply = DEAD
    await fillAndSubmit('Qa-second-5678', 'Qa-second-5678')
    expect(text()).toContain('Ссылка недействительна')
    expect(passwordFields()).toHaveLength(0)
  })

  it('отказ сервера по паролю показан строкой, форма остаётся', async () => {
    await showReset()
    submitReply = {
      status: 422,
      body: { error: { code: 'validation', message: 'Пароль слишком простой', details: {} } },
    }
    await fillAndSubmit('Qa-second-5678', 'Qa-second-5678')
    expect(text()).toContain('Пароль слишком простой')
    expect(passwordFields()).toHaveLength(2)
  })

  it('уже вошедшему в другую учётную запись ссылка молча не применяется', async () => {
    auth.me = { full_name: 'Елена Руководитель' }
    await showReset()
    expect(text()).toContain('Вы уже вошли в систему')
    expect(text()).toContain('Елена Руководитель')
    expect(passwordFields()).toHaveLength(0)
    await act(async () => {
      byText('button', 'Выйти и продолжить')!.click()
    })
    expect(auth.logout).toHaveBeenCalledTimes(1)
  })
})

describe('страница входа: восстановление доступа', () => {
  it('честная подсказка вместо кнопки «Забыли пароль?»', async () => {
    await act(async () => {
      root.render(createElement(MemoryRouter, null, createElement(LoginPage)))
    })
    expect(text()).toContain('Забыли пароль? Попросите руководителя выдать ссылку для нового пароля')
    // Ничего кликабельного про «забыли» нет — кнопка-обманка была бы дефектом
    const clickable = Array.from(document.body.querySelectorAll('button, a'))
    expect(clickable.some((el) => /забыли|восстанов/i.test(el.textContent ?? ''))).toBe(false)
    // Старая фраза про повторное приглашение исчезла: это невозможно для принявших
    expect(text()).not.toContain('отправить повторно')
  })
})
