import { type ButtonHTMLAttributes } from 'react'

/** Shared button. Variants match the hand-coded Tailwind patterns
 * used across the app so adopting it is a no-visual-change refactor. */

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md'

const VARIANT: Record<Variant, string> = {
  primary:
    'bg-accent text-white hover:bg-accent-hover',
  secondary:
    'bg-surface-tertiary text-text-primary border border-border ' +
    'hover:border-accent',
  danger:
    'bg-danger text-white hover:bg-danger/80',
  ghost:
    'bg-transparent text-text-secondary hover:text-text-primary ' +
    'hover:bg-surface-tertiary',
}

const SIZE: Record<Size, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

export function Button({
  variant = 'primary',
  size = 'md',
  className = '',
  ...rest
}: ButtonProps) {
  return (
    <button
      className={
        `${VARIANT[variant]} ${SIZE[size]} rounded-lg font-medium ` +
        'transition-colors cursor-pointer disabled:opacity-50 ' +
        `disabled:cursor-not-allowed ${className}`
      }
      {...rest}
    />
  )
}
