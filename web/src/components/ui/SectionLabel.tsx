import { type ReactNode } from 'react'

/** An uppercase micro-label above a group of content. Mirrors the phone's
 * SectionLabel. */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <p className="text-[11px] font-semibold uppercase tracking-wider text-text-muted mb-2">
      {children}
    </p>
  )
}
