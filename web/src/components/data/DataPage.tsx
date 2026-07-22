import { useEffect, useMemo } from 'react'
import { useData } from '../../hooks/useData'
import { TableBrowser } from './TableBrowser'
import { RecordEditor } from './RecordEditor'
import { DataEntryForm } from './DataEntryForm'
import { type StatusInfo } from '../../App'

interface Props {
  status: StatusInfo
}

// Icons for each table
const TABLE_ICONS: Record<string, string> = {
  projects: '📁',
  organizations: '🏢',
  time_entries: '⏱️',
  notes: '📝',
  people: '👥',
  sessions: '💬',
  activities: '📊',
  searches: '🔍',
  computers: '💻',
  files: '📎',
  pet_interactions: '🐾',
}

export function DataPage({ status }: Props) {
  const data = useData()

  // Load tables on mount
  useEffect(() => {
    data.fetchTables()
  }, [])

  // Load rows when table changes
  useEffect(() => {
    if (data.activeTable) {
      data.fetchRows(data.activeTable)
    }
  }, [data.activeTable])

  const handleTableChange = (table: string) => {
    data.setActiveTable(table)
    data.setEditingRecord(null)
  }

  const handleRefresh = () => {
    data.fetchRows(data.activeTable)
    data.fetchTables() // refresh counts
  }

  // Summary stats
  const stats = useMemo(() => {
    const totalRows = data.tables.reduce((sum, t) => sum + t.count, 0)
    const totalTables = data.tables.length
    const activeCount = data.tables.find(t => t.name === data.activeTable)?.count ?? 0
    return { totalRows, totalTables, activeCount }
  }, [data.tables, data.activeTable])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h2 className="text-base font-semibold text-text-primary">
              Database Explorer
            </h2>
            <span
              className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${
                status.coreOnline
                  ? 'bg-success/20 text-success'
                  : 'bg-text-muted/20 text-text-muted'
              }`}
            >
              {status.coreOnline && (
                <span className="inline-block w-1.5 h-1.5 bg-current rounded-full animate-pulse" />
              )}
              {status.coreOnline ? 'Connected' : 'Offline'}
            </span>
          </div>
          <div className="flex items-center gap-3">
            {/* Mini stats */}
            <div className="hidden md:flex items-center gap-4 text-xs text-text-muted mr-2">
              <span>{stats.totalTables} tables</span>
              <span className="text-border">|</span>
              <span>{stats.totalRows.toLocaleString()} total rows</span>
            </div>
            <button
              onClick={handleRefresh}
              disabled={data.loading}
              className="px-3 py-1.5 rounded-md text-xs font-medium bg-surface-tertiary text-text-secondary hover:text-text-primary transition-colors cursor-pointer disabled:opacity-50"
            >
              {data.loading ? (
                <span className="animate-pulse">Refreshing...</span>
              ) : (
                '↻ Refresh'
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Table tabs */}
      <div className="px-4 py-2 border-b border-border bg-surface-secondary/50 overflow-x-auto">
        <div className="flex gap-1">
          {data.tables.map(t => {
            const isActive = data.activeTable === t.name
            const icon = TABLE_ICONS[t.name] || '📋'
            return (
              <button
                key={t.name}
                onClick={() => handleTableChange(t.name)}
                className={`group relative px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap cursor-pointer ${
                  isActive
                    ? 'bg-accent text-white shadow-sm shadow-accent/25'
                    : 'text-text-secondary hover:text-text-primary hover:bg-surface-tertiary'
                }`}
              >
                <span className="mr-1.5">{icon}</span>
                {t.label}
                <span
                  className={`ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${
                    isActive
                      ? 'bg-white/20'
                      : 'bg-surface-tertiary group-hover:bg-surface'
                  }`}
                >
                  {t.count.toLocaleString()}
                </span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Quick add form */}
      <DataEntryForm
        activeTable={data.activeTable}
        onSave={data.saveRecord}
        onRefresh={handleRefresh}
      />

      {/* Main content: table + editor */}
      <div className="flex-1 flex overflow-hidden">
        {/* Table browser */}
        <div className={`flex-1 overflow-hidden ${data.editingRecord ? 'border-r border-border' : ''}`}>
          <TableBrowser
            rows={data.rows}
            loading={data.loading}
            activeTable={data.activeTable}
            onRowClick={row => data.setEditingRecord(row)}
          />
        </div>

        {/* Editor panel */}
        {data.editingRecord && (
          <div className="w-[420px] shrink-0">
            <RecordEditor
              record={data.editingRecord}
              table={data.activeTable}
              onSave={data.saveRecord}
              onDelete={data.deleteRecord}
              onClose={() => data.setEditingRecord(null)}
              onRefresh={handleRefresh}
            />
          </div>
        )}
      </div>
    </div>
  )
}
