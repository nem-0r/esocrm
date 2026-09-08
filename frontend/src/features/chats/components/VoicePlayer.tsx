import { Pause, Play } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { playerTime } from '@/shared/lib/format'

/**
 * Как в Telegram: начавшийся плеер ставит на паузу предыдущий. Модульная
 * переменная, а не контекст — плееров в открытом чате может быть много,
 * а координировать между ними нужно только «кто сейчас играет».
 */
let currentlyPlaying: HTMLAudioElement | null = null

/**
 * Голосовое сообщение. `src` отдаёт `/api/v1/files/{id}` — доступ проверен
 * сервером, а не публичной ссылкой, и браузер сам приложит cookie сессии:
 * дополнительный fetch с ручной авторизацией тут не нужен.
 *
 * Файл скачивается один раз: сервер отдаёт вложение с бессрочным
 * `Cache-Control: immutable` (содержимое по этому id не меняется никогда),
 * поэтому повторное воспроизведение и даже перезагрузка страницы берут звук
 * из кэша браузера, а не качают заново.
 */
export function VoicePlayer({
  src,
  durationSec,
}: {
  src: string
  durationSec: number | null
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playing, setPlaying] = useState(false)
  const [position, setPosition] = useState(0)
  const [duration, setDuration] = useState(durationSec ?? 0)

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const onTime = () => setPosition(audio.currentTime)
    const onDuration = () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) setDuration(audio.duration)
    }
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onEnd = () => setPosition(0)
    audio.addEventListener('timeupdate', onTime)
    audio.addEventListener('loadedmetadata', onDuration)
    audio.addEventListener('play', onPlay)
    audio.addEventListener('pause', onPause)
    audio.addEventListener('ended', onEnd)
    return () => {
      audio.removeEventListener('timeupdate', onTime)
      audio.removeEventListener('loadedmetadata', onDuration)
      audio.removeEventListener('play', onPlay)
      audio.removeEventListener('pause', onPause)
      audio.removeEventListener('ended', onEnd)
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
      void audio.play()
    } else {
      audio.pause()
    }
  }

  function seek(event: React.ChangeEvent<HTMLInputElement>) {
    const audio = audioRef.current
    if (!audio || !duration) return
    const next = (Number(event.target.value) / 100) * duration
    audio.currentTime = next
    setPosition(next)
  }

  const progress = duration ? (position / duration) * 100 : 0
  const shownSeconds = playing || position > 0 ? position : duration

  return (
    <div className="flex w-full max-w-64 items-center gap-2.5 rounded bg-black/25 px-2.5 py-2">
      {/* preload="none": в открытом чате таких сообщений может быть много,
          качать звук стоит только по нажатию, а не при каждом рендере ленты. */}
      <audio ref={audioRef} src={src} preload="none" />
      <button
        type="button"
        onClick={toggle}
        className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-text text-white transition-opacity hover:opacity-90"
        aria-label={playing ? 'Пауза' : 'Слушать'}
      >
        {playing ? (
          <Pause className="size-3.5 fill-current" aria-hidden />
        ) : (
          <Play className="ml-0.5 size-3.5 fill-current" aria-hidden />
        )}
      </button>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <input
          type="range"
          min={0}
          max={100}
          value={progress}
          onChange={seek}
          className="h-1 w-full cursor-pointer accent-accent-text"
          aria-label="Позиция воспроизведения"
        />
        <span className="text-micro tabular-nums text-ink-faint">{playerTime(shownSeconds)}</span>
      </div>
    </div>
  )
}
