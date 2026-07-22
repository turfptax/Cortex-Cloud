import { useState, useCallback } from 'react'
import { apiFetch } from '../lib/api'

export interface TableInfo {
  name: string
  count: number
  label: string
}

const TABLE_LABELS: Record<string, string> = {
  projects: 'Projects',
  organizations: 'Organizations',
  time_entries: 'Time Entries',
  notes: 'Notes',
  people: 'People',
  sessions: 'Sessions',
  activities: 'Activities',
  searches: 'Searches',
  computers: 'Computers',
  files: 'Files',
  pet_interactions: 'Pet Interactions',
}

// Default sort columns per table
const DEFAULT_ORDER: Record<string, string> = {
  projects: 'total_hours DESC',
  organizations: 'name ASC',
  time_entries: 'started_at DESC',
  notes: 'created_at DESC',
  people: 'name ASC',
  sessions: 'started_at DESC',
  activities: 'created_at DESC',
  searches: 'created_at DESC',
  computers: 'hostname ASC',
  files: 'created_at DESC',
  pet_interactions: 'created_at DESC',
}

export function useData() {
  const [tables, setTables] = useState<TableInfo[]>([])
  const [activeTable, setActiveTable] = useState('projects')
  const [rows, setRows] = useState<Record<string, any>[]>([])
  const [loading, setLoading] = useState(false)
  const [editingRecord, setEditingRecord] = useState<Record<string, any> | null>(null)
  const [tablesLoading, setTablesLoading] = useState(false)

  const fetchTables = useCallback(async () => {
    setTablesLoading(true)
    try {
      const result = await apiFetch<{ data: Record<string, number> }>('/data/tables')
      const counts = result.data || {}
      const list: TableInfo[] = Object.entries(counts)
        .map(([name, count]) => ({
          name,
          count,
          label: TABLE_LABELS[name] || name,
        }))
        .filter(t => t.count > 0 || ['projects', 'organizations', 'time_entries', 'notes'].includes(t.name))
        .sort((a, b) => b.count - a.count)
      setTables(list)
    } catch (err) {
      console.error('Failed to fetch tables:', err)
    }
    setTablesLoading(false)
  }, [])

  const fetchRows = useCallback(async (
    table?: string,
    filters?: Record<string, any>,
    orderBy?: string,
    limit?: number,
  ) => {
    const t = table || activeTable
    setLoading(true)
    try {
      const result = await apiFetch<{ rows: Record<string, any>[]; count: number }>('/data/query', {
        method: 'POST',
        body: JSON.stringify({
          table: t,
          filters: filters || null,
          limit: limit || 100,
          order_by: orderBy || DEFAULT_ORDER[t] || 'created_at DESC',
        }),
      })
      setRows(result.rows || [])
    } catch (err) {
      console.error('Failed to fetch rows:', err)
      setRows([])
    }
    setLoading(false)
  }, [activeTable])

  const saveRecord = useCallback(async (table: string, data: Record<string, any>) => {
    try {
      const result = await apiFetch<{ data: any; error?: string }>('/data/upsert', {
        method: 'POST',
        body: JSON.stringify({ table, data }),
      })
      if (result.error) {
        console.error('Upsert error:', result.error)
        return false
      }
      return true
    } catch (err) {
      console.error('Failed to save record:', err)
      return false
    }
  }, [])

  const deleteRecord = useCallback(async (table: string, id: string | number) => {
    try {
      const result = await apiFetch<{ data: any; error?: string }>('/data/delete', {
        method: 'POST',
        body: JSON.stringify({ table, id }),
      })
      if (result.error) {
        console.error('Delete error:', result.error)
        return false
      }
      return true
    } catch (err) {
      console.error('Failed to delete record:', err)
      return false
    }
  }, [])

  return {
    tables,
    tablesLoading,
    activeTable,
    setActiveTable,
    rows,
    loading,
    fetchTables,
    fetchRows,
    saveRecord,
    deleteRecord,
    editingRecord,
    setEditingRecord,
  }
}
