interface TagProps {
  label: string
  color?: string
}

/** A small colored label, e.g. a corpus-kind badge. `color` is a hex string
 * (see kindColor); when omitted it falls back to a neutral token. Mirrors the
 * phone's Tag. */
export function Tag({ label, color }: TagProps) {
  if (color) {
    return (
      <span
        className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium"
        style={{ color, backgroundColor: `${color}22` }}
      >
        {label}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-surface-tertiary text-text-secondary">
      {label}
    </span>
  )
}
