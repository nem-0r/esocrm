/**
 * Живые обновления.
 *
 * При обрыве соединение восстанавливается с нарастающей паузой, а подписчики
 * получают сигнал «переподключились» — по нему экраны перезапрашивают данные.
 * Показывать устаревшее молча нельзя: пользователь должен видеть либо свежее,
 * либо явную плашку «нет связи».
 */

import type { WsEvent } from '@/entities/types'

type Handler = (event: WsEvent) => void
type StatusHandler = (status: WsStatus) => void

export type WsStatus = 'connecting' | 'online' | 'offline'

const RECONNECT_STEPS = [1000, 2000, 5000, 10000, 20000]
const PING_INTERVAL = 25000

class RealtimeClient {
  private socket: WebSocket | null = null
  private handlers = new Set<Handler>()
  private statusHandlers = new Set<StatusHandler>()
  private attempt = 0
  private pingTimer: ReturnType<typeof setInterval> | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private stopped = true
  private currentStatus: WsStatus = 'offline'

  get status() {
    return this.currentStatus
  }

  connect() {
    this.stopped = false
    this.open()
  }

  disconnect() {
    this.stopped = true
    this.clearTimers()
    this.socket?.close()
    this.socket = null
    this.setStatus('offline')
  }

  subscribe(handler: Handler) {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  onStatus(handler: StatusHandler) {
    this.statusHandlers.add(handler)
    handler(this.currentStatus)
    return () => this.statusHandlers.delete(handler)
  }

  /** Сообщает серверу, какой чат открыт — чтобы в шапке было видно, кто ещё здесь. */
  setViewing(conversationId: number | null) {
    if (conversationId === null) return
    this.send({ type: 'viewing', conversation_id: conversationId })
  }

  private send(payload: unknown) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload))
    }
  }

  private setStatus(status: WsStatus) {
    if (this.currentStatus === status) return
    this.currentStatus = status
    this.statusHandlers.forEach((h) => h(status))
  }

  private open() {
    if (this.stopped) return
    this.clearTimers()
    this.setStatus(this.attempt === 0 ? 'connecting' : 'connecting')

    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${scheme}://${window.location.host}/api/v1/ws`)
    this.socket = socket

    socket.onopen = () => {
      this.attempt = 0
      this.setStatus('online')
      this.pingTimer = setInterval(() => this.send({ type: 'ping' }), PING_INTERVAL)
    }

    socket.onmessage = (raw) => {
      let event: WsEvent
      try {
        event = JSON.parse(raw.data as string) as WsEvent
      } catch {
        return
      }
      if (event.type === 'pong') return
      this.handlers.forEach((handler) => handler(event))
    }

    socket.onclose = () => {
      this.socket = null
      this.clearTimers()
      if (this.stopped) return
      this.setStatus('offline')
      const delay = RECONNECT_STEPS[Math.min(this.attempt, RECONNECT_STEPS.length - 1)]
      this.attempt += 1
      this.reconnectTimer = setTimeout(() => this.open(), delay)
    }

    socket.onerror = () => socket.close()
  }

  private clearTimers() {
    if (this.pingTimer) clearInterval(this.pingTimer)
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    this.pingTimer = null
    this.reconnectTimer = null
  }
}

export const realtime = new RealtimeClient()
