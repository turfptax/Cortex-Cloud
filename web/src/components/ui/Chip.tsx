interface ChipProps {
  label: string
  selected?: boolean
  onClick?: () => void
}

/** A selectable pill, for filters and browse-by-kind. Mirrors the phone's Chip. */
export function Chip({ label, selected = false, onClick }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors cursor-pointer ${
        selected
          ? 'bg-accent/15 border-accent/40 text-accent-hover'
          : 'bg-surface-secondary border-border text-text-secondary hover:text-text-primary hover:border-text-muted'
      }`}
    >
      {label}
    </button>
  )
}
