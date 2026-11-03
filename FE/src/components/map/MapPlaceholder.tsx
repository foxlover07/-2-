import { MapPinOff } from 'lucide-react'

interface MapPlaceholderProps {
  message: string
  detail?: string
}

export function MapPlaceholder({ message, detail }: MapPlaceholderProps) {
  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-2 bg-bg px-6 text-center"
      style={{
        backgroundImage:
          'linear-gradient(var(--color-border) 1px, transparent 1px), linear-gradient(90deg, var(--color-border) 1px, transparent 1px)',
        backgroundSize: '32px 32px',
      }}
    >
      <div className="flex size-12 items-center justify-center rounded-full bg-white text-muted shadow-sm">
        <MapPinOff className="size-6" aria-hidden />
      </div>
      <p className="rounded-control bg-white/90 px-3 py-1 text-sm font-medium text-text">{message}</p>
      {detail && <p className="max-w-xs rounded-control bg-white/90 px-3 py-1 text-xs text-muted">{detail}</p>}
    </div>
  )
}
