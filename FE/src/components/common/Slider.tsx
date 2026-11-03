import { useId, type ReactNode } from 'react'
import { cx } from '../../utils/cx'

interface SliderProps {
  label?: ReactNode
  value: number
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
  formatValue?: (value: number) => ReactNode
  minLabel?: ReactNode
  maxLabel?: ReactNode
  className?: string
}

export function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  onChange,
  formatValue = (v) => v,
  minLabel,
  maxLabel,
  className,
}: SliderProps) {
  const id = useId()

  return (
    <div className={cx('w-full', className)}>
      {label && (
        <div className="mb-2 flex items-center justify-between gap-2">
          <label htmlFor={id} className="text-sm font-medium text-text">
            {label}
          </label>
          <span className="text-sm font-bold text-blue">{formatValue(value)}</span>
        </div>
      )}
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-2 w-full cursor-pointer accent-blue"
      />
      {(minLabel || maxLabel) && (
        <div className="mt-1 flex justify-between text-xs text-muted">
          <span>{minLabel}</span>
          <span>{maxLabel}</span>
        </div>
      )}
    </div>
  )
}
