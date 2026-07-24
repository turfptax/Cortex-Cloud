import { type InputHTMLAttributes } from 'react'

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
}

/** A labeled text input. Mirrors the phone's Field. */
export function Field({ label, className = '', ...props }: FieldProps) {
  return (
    <label className="block">
      {label && (
        <span className="block text-xs font-medium text-text-muted mb-1">{label}</span>
      )}
      <input
        {...props}
        className={`w-full px-3 py-2 rounded-lg bg-surface-secondary border border-border text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/60 ${className}`}
      />
    </label>
  )
}
