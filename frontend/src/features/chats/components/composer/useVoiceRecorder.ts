import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Запись голосового с микрофона.
 *
 * Браузер пишет в том формате, который умеет: Firefox — Ogg/Opus, Chrome и
 * Edge — WebM/Opus, Safari — MP4/AAC. Сервер всё равно перекодирует в формат
 * голосовых Telegram, поэтому здесь важно одно — чтобы запись вообще шла.
 *
 * Кнопка микрофона показывается, только если запись возможна в принципе
 * (`isRecordingSupported`): «ни одной кнопки-обманки».
 */

export type RecorderState = 'idle' | 'requesting' | 'recording' | 'error'

// Час записи — предел здравого смысла для голосового; дальше это уже лекция,
// и её лучше отправить файлом.
export const MAX_RECORDING_SECONDS = 60 * 60

// Как на сервере (file_service.MIN_VOICE_SECONDS): короче секунды — случайное
// нажатие. Не грузим впустую и объясняем, что произошло.
export const MIN_RECORDING_SECONDS = 1

const MIME_CANDIDATES = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/mp4', 'audio/webm']

export function isRecordingSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof navigator !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia) &&
    typeof window.MediaRecorder !== 'undefined'
  )
}

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return undefined
  return MIME_CANDIDATES.find((type) => MediaRecorder.isTypeSupported(type))
}

/** Человеческий текст вместо DOMException. */
export function microphoneError(cause: unknown): string {
  const name = cause instanceof DOMException ? cause.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Нет доступа к микрофону. Разрешите его в настройках браузера — значок замка слева от адреса сайта.'
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'Микрофон не найден. Подключите его и попробуйте снова.'
  }
  if (name === 'NotReadableError' || name === 'AbortError') {
    return 'Микрофон занят другим приложением (звонок, запись). Освободите его и попробуйте снова.'
  }
  return 'Не удалось начать запись. Попробуйте ещё раз.'
}

export function useVoiceRecorder(
  onRecorded: (blob: Blob, seconds: number, sendNow: boolean) => void,
) {
  const [state, setState] = useState<RecorderState>('idle')
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)
  // Последний обработчик без пересоздания записи на каждый рендер.
  const recorded = useRef(onRecorded)
  useEffect(() => {
    recorded.current = onRecorded
  }, [onRecorded])

  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const context = useRef<AudioContext | null>(null)
  const frame = useRef<number | null>(null)
  const timer = useRef<number | null>(null)
  const startedAt = useRef(0)
  const discard = useRef(false)
  // «Отправить» прямо из полосы записи: после остановки сообщение уходит само.
  const sendNow = useRef(false)

  const teardown = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current)
    if (timer.current !== null) window.clearInterval(timer.current)
    frame.current = null
    timer.current = null
    stream.current?.getTracks().forEach((track) => track.stop())
    stream.current = null
    void context.current?.close().catch(() => undefined)
    context.current = null
    setLevel(0)
  }, [])

  const stop = useCallback(() => {
    const active = recorder.current
    if (active && active.state !== 'inactive') active.stop()
  }, [])

  /** Остановить и сразу отправить — как стрелка в Telegram. */
  const stopAndSend = useCallback(() => {
    sendNow.current = true
    stop()
  }, [stop])

  const start = useCallback(async () => {
    if (!isRecordingSupported()) {
      setError('Этот браузер не умеет записывать звук')
      setState('error')
      return
    }
    setError(null)
    setSeconds(0)
    setState('requesting')
    discard.current = false
    try {
      const media = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      stream.current = media
      const mimeType = pickMimeType()
      const instance = new MediaRecorder(
        media,
        mimeType ? { mimeType, audioBitsPerSecond: 64000 } : undefined,
      )
      chunks.current = []
      instance.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.current.push(event.data)
      }
      instance.onstop = () => {
        const type = instance.mimeType || mimeType || 'audio/webm'
        const result = new Blob(chunks.current, { type })
        // Длительность считаем сами: Chrome пишет запись без неё (у файла
        // «бесконечная» длина), и превью показало бы 0:00 до ответа сервера.
        const recordedSeconds = (Date.now() - startedAt.current) / 1000
        const wantSend = sendNow.current
        sendNow.current = false
        chunks.current = []
        teardown()
        recorder.current = null
        setState('idle')
        setSeconds(0)
        if (discard.current || result.size === 0) return
        if (recordedSeconds < MIN_RECORDING_SECONDS) {
          setError('Запись слишком короткая — говорите хотя бы секунду и нажмите «Стоп»')
          return
        }
        recorded.current(result, recordedSeconds, wantSend)
      }
      recorder.current = instance
      instance.start(250)
      startedAt.current = Date.now()
      setState('recording')

      timer.current = window.setInterval(() => {
        const elapsed = (Date.now() - startedAt.current) / 1000
        setSeconds(elapsed)
        if (elapsed >= MAX_RECORDING_SECONDS) stop()
      }, 200)

      // Уровень громкости — живой индикатор, что микрофон слышит.
      try {
        const AudioCtx =
          window.AudioContext ??
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
        if (AudioCtx) {
          const audio = new AudioCtx()
          context.current = audio
          const analyser = audio.createAnalyser()
          analyser.fftSize = 512
          audio.createMediaStreamSource(media).connect(analyser)
          const data = new Uint8Array(analyser.fftSize)
          const tick = () => {
            analyser.getByteTimeDomainData(data)
            let peak = 0
            for (const value of data) peak = Math.max(peak, Math.abs(value - 128))
            setLevel(Math.min(1, peak / 64))
            frame.current = requestAnimationFrame(tick)
          }
          tick()
        }
      } catch {
        // Индикатор — украшение: без него запись всё равно идёт.
      }
    } catch (cause) {
      teardown()
      setError(microphoneError(cause))
      setState('error')
    }
  }, [stop, teardown])

  /** Удалить запись: остановить и выбросить. */
  const cancel = useCallback(() => {
    discard.current = true
    sendNow.current = false
    const active = recorder.current
    if (active && active.state !== 'inactive') {
      active.stop()
    } else {
      teardown()
      setState('idle')
    }
    setSeconds(0)
  }, [teardown])

  useEffect(
    () => () => {
      discard.current = true
      const active = recorder.current
      if (active && active.state !== 'inactive') active.stop()
      teardown()
    },
    [teardown],
  )

  return { state, seconds, level, error, start, stop, stopAndSend, cancel }
}
