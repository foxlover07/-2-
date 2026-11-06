import { Minus, Plus } from 'lucide-react'
import { cx } from '../../utils/cx'

interface StepperProps {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  label: string
  unit?: string
  disabled?: boolean
  step?: number
}

const BUTTON =
  'flex size-8 items-center justify-center rounded-control border border-border bg-white text-text transition-colors hover:bg-bg disabled:cursor-not-allowed disabled:text-border disabled:hover:bg-white'

export function Stepper({ value, min, max, onChange, label, unit = '', disabled = false, step = 1 }: StepperProps) {
  return (
    <div className={cx('flex items-center gap-2', disabled && 'opacity-50')}>
      <button
        type="button"
        className={BUTTON}
        aria-label={`${label} 줄이기`}
        disabled={disabled || value <= min}
        onClick={() => onChange(Math.max(min, value - step))}
      >
        <Minus className="size-4" aria-hidden />
      </button>
      <output aria-label={label} aria-live="polite" className="w-14 text-center text-sm font-bold text-text">
        {value}
        {unit}
      </output>
      <button
        type="button"
        className={BUTTON}
        aria-label={`${label} 늘리기`}
        disabled={disabled || value >= max}
        onClick={() => onChange(Math.min(max, value + step))}
      >
        <Plus className="size-4" aria-hidden />
      </button>
    </div>
  )
}
