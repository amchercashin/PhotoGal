/**
 * Left sidebar: sources management.
 */

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { open } from '@tauri-apps/plugin-dialog'
import { api, type Source } from '../../api/client'
import { useUIStore } from '../../store/ui'
import { toast } from '../../store/toast'

const isTauri = '__TAURI_INTERNALS__' in window

export function Sidebar() {
  const qc = useQueryClient()
  const isRunning = useUIStore((s) => s.isPipelineRunning)
  const [newPath, setNewPath] = useState('')
  const [showAddSource, setShowAddSource] = useState(false)

  const { data: sources = [] } = useQuery({
    queryKey: ['sources'],
    queryFn: api.getSources,
    refetchInterval: isRunning ? 5000 : 30_000,
  })

  const runLevel = useMutation({
    mutationFn: ({ level, source_id }: { level: number; source_id?: number }) =>
      api.runLevel(level, source_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pipeline-status'] }),
  })

  const addSource = useMutation({
    mutationFn: (path: string) => api.addSource(path),
    onSuccess: (source) => {
      qc.invalidateQueries({ queryKey: ['sources'] })
      setNewPath('')
      setShowAddSource(false)
      // Auto-start L0 scan for the newly added source
      runLevel.mutate({ level: 0, source_id: source.id })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const deleteSource = useMutation({
    mutationFn: (source_id: number) => api.removeSource(source_id, true),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sources'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['clusters'] })
      qc.invalidateQueries({ queryKey: ['photos-table'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  async function handleAddSource() {
    if (isTauri) {
      const selected = await open({
        directory: true,
        multiple: false,
        title: 'Select photo folder',
      })
      if (selected) addSource.mutate(selected)
    } else {
      setShowAddSource(!showAddSource)
    }
  }

  return (
    <div className="flex flex-col h-full bg-neutral-900 border-r border-neutral-800 text-sm">
      {/* Sources section */}
      <div className="flex-1 overflow-y-auto p-3">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-neutral-400 text-xs font-semibold uppercase tracking-wider">Sources</h3>
          <button
            className="text-neutral-400 hover:text-white text-lg leading-none"
            onClick={handleAddSource}
            title="Add source folder"
          >+</button>
        </div>

        {!isTauri && showAddSource && (
          <div className="mb-3 flex flex-col gap-1">
            <input
              className="text-xs bg-neutral-800 border border-neutral-600 rounded px-2 py-1.5 text-white w-full"
              placeholder="/path/to/photos"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && newPath && addSource.mutate(newPath)}
            />
            <button
              className="text-xs bg-blue-700 hover:bg-blue-600 text-white rounded px-2 py-1"
              onClick={() => newPath && addSource.mutate(newPath)}
            >
              Add
            </button>
          </div>
        )}

        <div className="space-y-1">
          {sources.map((s) => (
            <SourceItem
              key={s.id}
              source={s}
              onScan={() => runLevel.mutate({ level: 0, source_id: s.id })}
              onDelete={() => deleteSource.mutate(s.id)}
              disabled={!!isRunning}
            />
          ))}
          {sources.length === 0 && (
            <p className="text-neutral-600 text-xs mt-2">No sources added yet.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function SourceItem({
  source, onScan, onDelete, disabled,
}: {
  source: Source; onScan: () => void; onDelete: () => void; disabled: boolean
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <div className="flex items-center gap-2 rounded px-2 py-1.5 bg-neutral-800">
      <div className="flex-1 min-w-0">
        <div className="text-neutral-200 truncate text-xs">{source.name || source.path.split('/').pop()}</div>
        <div className="text-neutral-500 text-[10px] truncate">{source.path}</div>
        <div className="text-neutral-500 text-[10px]">{source.photo_count} photos · {source.status}</div>
      </div>
      {confirmDelete ? (
        <div className="flex items-center gap-1 shrink-0">
          <button
            className="text-[10px] bg-red-700 hover:bg-red-600 text-white rounded px-1.5 py-1"
            onClick={() => { setConfirmDelete(false); onDelete() }}
          >Yes</button>
          <button
            className="text-[10px] bg-neutral-700 hover:bg-neutral-600 text-neutral-300 rounded px-1.5 py-1"
            onClick={() => setConfirmDelete(false)}
          >No</button>
        </div>
      ) : (
        <div className="flex items-center gap-1 shrink-0">
          <button
            className="text-xs bg-blue-900/60 hover:bg-blue-800 text-blue-300 rounded px-1.5 py-1 disabled:opacity-30"
            onClick={onScan}
            disabled={disabled}
            title="Scan (Level 0)"
          >↻</button>
          <button
            className="text-xs bg-red-900/40 hover:bg-red-800/60 text-red-400 rounded px-1.5 py-1 disabled:opacity-30"
            onClick={() => setConfirmDelete(true)}
            disabled={disabled}
            title="Delete source and all its photos"
          >×</button>
        </div>
      )}
    </div>
  )
}
