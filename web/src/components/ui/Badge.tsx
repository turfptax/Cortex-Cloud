import { type HTMLAttributes } from 'react'

/** Small status pill. Tones map to the theme's semantic colors. */

type Tone = 'neutral' | 'success' | 'warning' | 'danger' | 'accent'

const TONE: Record<Tone, string> = {
  neutral: 'bg-surface-tertiary text-text-secondary',
  success: 'bg-success/15 text-success',
  warning: 'bg-warning/15 text-warning',
  danger: 'bg-danger/15 text-danger',
  accent: 'bg-accent/15 text-accent',
}

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
}

export function Badge({ tone = 'neutral', className = '', ...rest }: BadgeProps) {
  return (
    <span
      className={
        `${TONE[tone]} inline-flex items-center px-2 py-0.5 ` +
        `rounded-full text-xs font-medium ${className}`
      }
      {...rest}
    />
  )
}
