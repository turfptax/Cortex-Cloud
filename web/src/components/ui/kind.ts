// Corpus-kind color + label, mirrored from the phone app (src/ui/theme.ts
// kindColor, src/ui/format.ts kindLabel) so the web and phone speak the same
// visual language. Hex values are used as tinted pill backgrounds via Tag.
export const kindColor: Record<string, string> = {
  gist: '#4cc2ff',
  theme: '#b388ff',
  pattern: '#ffb454',
  drift: '#ff8fa3',
  question: '#7ee787',
  journal_entry: '#79c0ff',
  human_journal_entry: '#79c0ff',
  temporal_narrative: '#d2a8ff',
  note: '#e3b341',
  project: '#4cc2ff',
  skill: '#7ee787',
  rule: '#ffb454',
}

export function colorForKind(kind: string): string {
  return kindColor[kind] ?? '#95a2b0'
}

const KIND_LABELS: Record<string, string> = {
  gist: 'Gist',
  theme: 'Theme',
  pattern: 'Pattern',
  drift: 'Drift',
  question: 'Question',
  journal_entry: 'Journal',
  human_journal_entry: 'Journal',
  temporal_narrative: 'Narrative',
  note: 'Note',
  project: 'Project',
  skill: 'Skill',
  rule: 'Rule',
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}
