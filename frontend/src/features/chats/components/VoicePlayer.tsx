import { Music, Pause, Play } from 'lucide-react'
import { useEffect, useRef, useState, type MouseEvent } from 'react'

import { cn } from '@/shared/lib/cn'
import { playerTime } from '@/shared/lib/format'

/**
 * Как в Telegram: начавшийся плеер ставит на паузу предыдущий. Модульная
 * переменная, а не контекст — плееров в открытом чате может быть много,
 * а координировать между ними нужно только «кто сейчас играет».
 */
let currentlyPlaying: HTMLAudioElement | null = null

/** Форматы, которые Safari (iPhone, Mac) не играет: для них есть AAC-копия. */
const NEEDS_SAFARI_COPY = new Set(['audio/ogg', 'audio/opus', 'audio/webm', 'application/ogg', 'video/ogg'])

const FLAT_WAVE = Array.from({ length: 48 }, () => 6)

/**
 * Голосовое сообщение или аудиофайл.
 *
 * `src` отдаёт `/api/v1/files/{id}` — доступ проверен сервером, браузер сам
 * приложит cookie сессии. Голосовые Telegram приходят в Ogg/Opus, которого
 * Safari не понимает: плеер указывает второй источник — AAC-копию
 * (`?variant=m4a`), и браузер сам выбирает, что умеет. Chrome и Firefox берут
 * оригинал, Safari — копию.
 *
 * Файл скачивается один раз: сервер отдаёт вложение с бессрочным
 * `Cache-Control: immutable`, повторное воспроизведение идёт из кэша.
 */
export function VoicePlayer({
  src,
  mimeType,
  durationSec,
  waveform,
  title,
  performer,
  local = false,
}: {
  src: string
  mimeType?: string | null
  durationSec: number | null
  waveform?: number[] | null
  /** Музыка: название и исполнитель вместо волны. */
  title?: string | null
  performer?: string | null
  /** Локальная запись (превью перед отправкой) — без серверной копии для Safari. */
  local?: boolean
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const pendingSeek = useRef<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [duration, setDuration] = useState(durationSec ?? 0)
  const [failed, setFailed] = useState(false)
  // Длительность может прийти позже, чем появился плеер: у превью записи её
  // сначала знает диктофон, потом точную присылает сервер после загрузки.
  const [knownDurationSec, setKnownDurationSec] = useState(durationSec)
  if (durationSec !== knownDurationSec) {
    setKnownDurationSec(durationSec)
    if (durationSec) setDuration(durationSec)
  }

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const onTime = () => setPosition(audio.currentTime)
    const onDuration = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) setDuration(audio.duration)
      if (pendingSeek.current !== null) {
        audio.currentTime = pendingSeek.current
        pendingSeek.current = null
      }
    }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnd = () => setPosition(0)
    const onError = () => setFailed(true)
    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('loadedmetadata', onDuration)
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnd)
    audio.addEventListener('error', onError)
    return () => {
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('loadedmetadata', onDuration)
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnd)
      audio.removeEventListener('error', onError)
      // Плеер закрылся играющим (ушли из чата) — не держим ссылку на
      // отмонтированный элемент, иначе следующий плеер решит, что играть
      // ещё есть чему, хотя ставить на паузу уже нечего.
      if (currentlyPlaying === audio) currentlyPlaying = null
    }
  }, [])

  function toggle() {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      if (currentlyPlaying && currentlyPlaying !== audio) currentlyPlaying.pause()
      currentlyPlaying = audio
      setFailed(false)
      audio.play().catch(() => setFailed(true))
    } else {
      audio.pause()
    }
  }

  function seekTo(fraction: number) {
    const audio = audioRef.current
    if (!audio || !duration) return
    const next = Math.max(0, Math.min(1, fraction)) * duration
    if (audio.readyState >= 1) {
      audio.currentTime = next
    } else {
      // Звук ещё не загружен: запоминаем место и начинаем воспроизведение —
      // перемотаем, как только станет известна длина.
      pendingSeek.current = next
      if (audio.paused) toggle()
    }
    setPosition(next)
  }

  function onWaveClick(event: MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    seekTo((event.clientX - rect.left) / rect.width)
  }

  const progress = duration ? position / duration : 0
  const shownSeconds = playing || position > 0 ? position : duration
  const bars = waveform && waveform.length ? compress(waveform, 48) : FLAT_WAVE
  const baseMime = (mimeType ?? '').split(';')[0].trim().toLowerCase()
  const isMusic = Boolean(title || performer) && !waveform

  return (
    <div className="flex w-64 max-w-full items-center gap-2.5 rounded bg-black/25 px-2.5 py-2">
      {/* preload="none": в открытом чате таких сообщений может быть много,
          качать звук стоит только по нажатию, а не при каждом рендере ленты. */}
      {local ? (
        <audio ref={audioRef} src={src} preload="metadata" />
      ) : (
        <audio ref={audioRef} preload="none">
          <source src={src} type={baseMime === 'audio/ogg' ? 'audio/ogg; codecs=opus' : baseMime || undefined} />
          {NEEDS_SAFARI_COPY.has(baseMime) && <source src={`${src}?variant=m4a`} type="audio/mp4" />}
        </audio>
      )}
      <button
        type="button"
        onClick={toggle}
        className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent-text text-white transition-opacity hover:opacity-90"
        aria-label={playing ? 'Пауза' : 'Слушать'}
      >
        {playing ? (
          <Pause className="size-4 fill-current" aria-hidden />
        ) : (
          <Play className="ml-0.5 size-4 fill-current" aria-hidden />
        )}
      </button>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {isMusic ? (
          <span className="flex min-w-0 items-center gap-1.5 text-xs text-ink">
            <Music className="size-3.5 shrink-0 text-ink-faint" aria-hidden />
            <span className="truncate">
              {[performer, title].filter(Boolean).join(' — ')}
            </span>
          </span>
        ) : null}
        <div
          role="slider"
          aria-label="Позиция воспроизведения"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(position)}
          tabIndex={0}
          onClick={onWaveClick}
          onKeyDown={(event) => {
            if (event.key === 'ArrowRight') seekTo(progress + 0.05)
            if (event.key === 'ArrowLeft') seekTo(progress - 0.05)
          }}
          className={cn('flex h-6 cursor-pointer items-center gap-px', isMusic && 'h-3')}
        >
          {bars.map((value, index) => {
            const played = (index + 0.5) / bars.length <= progress
            return (
              <span
                key={index}
                className={cn(
                  'w-full min-w-px flex-1 rounded-full transition-colors',
                  played ? 'bg-accent-text' : 'bg-ink-faint/50',
                )}
                style={{ height: `${Math.max(12, (value / 31) * 100)}%` }}
              />
            )
          })}
        </div>
        <span className="text-micro tabular-nums text-ink-faint">
          {failed ? 'Не удалось воспроизвести — скачайте файл' : playerTime(shownSeconds)}
        </span>
      </div>
    </div>
  )
}

/** Сжать волну до нужного числа столбиков, сохраняя пики (как Telegram). */
export function compress(values: number[], target: number): number[] {
  if (values.length <= target) return values
  const result: number[] = []
  for (let index = 0; index < target; index++) {
    const start = Math.floor((index * values.length) / target)
    const end = Math.max(start + 1, Math.floor(((index + 1) * values.length) / target))
    result.push(Math.max(...values.slice(start, end)))
  }
  return result
}
