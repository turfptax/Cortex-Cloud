import { useState, useCallback } from 'react'
import { apiFetch } from '../lib/api'

export function usePi() {
  const [notes, setNotes] = useState<any[]>([])

  const fetchNotes = useCallback(async (limit = 20) => {
    try {
      const data = await apiFetch(`/pi/notes?limit=${limit}`)
      setNotes(data?.results || data?.data || [])
    } catch {
      setNotes([])
    }
  }, [])

  const sendNote = useCallback(
    async (
      content: string,
      tags = '',
      project = '',
      noteType = 'note'
    ) => {
      try {
        await apiFetch('/pi/notes', {
          method: 'POST',
          body: JSON.stringify({
            content,
            tags,
            project,
            note_type: noteType,
          }),
        })
        await fetchNotes()
        return true
      } catch {
        return false
      }
    },
    [fetchNotes]
  )

  return {
    notes,
    fetchNotes,
    sendNote,
  }
}
