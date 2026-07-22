import { type HTMLAttributes, type ReactNode } from 'react'

/** Shared card surface: bg-surface-secondary + border, the dominant
 * panel pattern across the app. */

export interface CardProps
  extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  /** Header text or node (shadows the native title attribute). */
  title?: ReactNode
  /** Right-aligned header content (buttons, badges). */
  actions?: ReactNode
}

export function Card({
  title,
  actions,
  className = '',
  children,
  ...rest
}: CardProps) {
  return (
    <div
      className={
        'bg-surface-secondary border border-border rounded-xl p-5 ' +
        className
      }
      {...rest}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between mb-3">
          {title && (
            <h3 className="text-sm font-semibold text-text-primary">
              {title}
            </h3>
          )}
          {actions && <div className="flex gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  )
}
