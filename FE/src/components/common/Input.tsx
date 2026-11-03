import { useId, type InputHTMLAttributes, type ReactNode } from 'react'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: ReactNode
  suffix?: ReactNode
  hint?: ReactNode
}

export function Input({ label, suffix, hint, id, className, ...rest }: InputProps) {
  const autoId = useId()
  const inputId = id ?? autoId

  return (
    <div className={className}>
      {label && (
        <label htmlFor={inputId} className="mb-1.5 block text-sm font-medium text-text">
          {label}
        </label>
      )}
      <div className="flex h-10 items-center rounded-control border border-border bg-white px-3 focus-within:border-blue focus-within:ring-1 focus-within:ring-blue">
        <input
          id={inputId}
          className="w-full min-w-0 bg-transparent text-sm text-text outline-none placeholder:text-muted"
          {...rest}
        />
        {suffix && <span className="ml-2 shrink-0 text-sm text-muted">{suffix}</span>}
      </div>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}
