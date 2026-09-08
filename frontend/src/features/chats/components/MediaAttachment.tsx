import { X } from 'lucide-react'
import { useEffect, useState } from 'react'

/** Ширина/высота с сервера — плейсхолдер держит место в ленте, картинка не
 *  «прыгает» при подгрузке. Дальше сжимаем до разумного максимума на экране. */
function previewSize(width?: number | null, height?: number | null) {
  const MAX_W = 320
  const MAX_H = 320
  if (!width || !height) return { width: MAX_W, height: undefined }
  const scale = Math.min(1, MAX_W / width, MAX_H / height)
  return { width: Math.round(width * scale), height: Math.round(height * scale) }
}

export function ImagePreview({
  src,
  width,
  height,
  alt,
}: {
  src: string
  width?: number | null
  height?: number | null
  alt: string
}) {
  const [open, setOpen] = useState(false)
  const size = previewSize(width, height)

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="block overflow-hidden rounded-lg"
        style={{ width: size.width, maxWidth: '100%' }}
      >
        <img
          src={src}
          alt={alt}
          loading="lazy"
          width={width ?? undefined}
          height={height ?? undefined}
          style={{ aspectRatio: width && height ? `${width} / ${height}` : undefined }}
          className="block h-auto max-h-80 w-full object-cover"
        />
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4"
        >
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Закрыть"
            className="absolute right-4 top-4 flex size-9 items-center justify-center rounded-full bg-black/40 text-white hover:bg-black/60"
          >
            <X className="size-5" aria-hidden />
          </button>
          <img
            src={src}
            alt={alt}
            className="max-h-full max-w-full rounded-lg object-contain"
            onClick={(event) => event.stopPropagation()}
          />
        </div>
      )}
    </>
  )
}

export function VideoPreview({
  src,
  width,
  height,
}: {
  src: string
  width?: number | null
  height?: number | null
}) {
  const size = previewSize(width, height)
  return (
    <video
      src={src}
      controls
      preload="metadata"
      width={width ?? undefined}
      height={height ?? undefined}
      style={{
        width: size.width,
        maxWidth: '100%',
        aspectRatio: width && height ? `${width} / ${height}` : undefined,
      }}
      className="block max-h-80 rounded-lg bg-black"
    />
  )
}
