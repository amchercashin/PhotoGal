/**
 * Top navigation bar: tabs, zoom controls, stats, marked-photo actions.
 */

import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type Cluster, type ClusterListResult } from '../../api/client'
import { useUIStore, type Tab } from '../../store/ui'
import { useSelectionStore, parseItemKey } from '../../store/selection'
import { useDebounce } from '../../hooks/useDebounce'
import { AnalyzeDialog } from '../dialogs/AnalyzeDialog'
import { DeleteDialog } from '../dialogs/DeleteDialog'
import { CONTENT_CATEGORIES, TECHNICAL_CATEGORIES } from '../../constants/categories'

export function Topbar() {
  const { tab, setTab, zoom, zoomIn, zoomOut, openViewer } = useUIStore()
  const isPipelineRunning = useUIStore((s) => s.isPipelineRunning)
  const setSearchQuery = useUIStore((s) => s.setSearchQuery)
  const { getMarkedIds, clearMarks, markItems } = useSelectionStore()
  const qc = useQueryClient()

  const [showAnalyze, setShowAnalyze] = useState(false)
  const [showDelete, setShowDelete] = useState(false)

  const [searchInput, setSearchInput] = useState('')
  const [showDropdown, setShowDropdown] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const debouncedSearch = useDebounce(searchInput, 400)

  const filteredCategories = useMemo(() => {
    const q = searchInput.toLowerCase()
    if (!q) return { content: [...CONTENT_CATEGORIES], technical: [...TECHNICAL_CATEGORIES] }
    return {
      content: CONTENT_CATEGORIES.filter(c => c.key.includes(q) || c.label.toLowerCase().includes(q)),
      technical: TECHNICAL_CATEGORIES.filter(c => c.key.includes(q) || c.label.toLowerCase().includes(q)),
    }
  }, [searchInput])

  const hasFilteredCategories = filteredCategories.content.length > 0 || filteredCategories.technical.length > 0

  useEffect(() => {
    setSearchQuery(debouncedSearch)
    // Auto-switch to gallery when searching
    if (debouncedSearch && useUIStore.getState().tab !== 'gallery') {
      setTab('gallery')
    }
  }, [debouncedSearch, setSearchQuery, setTab])

  // Close dropdown on outside click
  useEffect(() => {
    if (!showDropdown) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showDropdown])

  const { data: stats } = useQuery({
    queryKey: ['stats'],
    queryFn: api.getStats,
    refetchInterval: isPipelineRunning ? 5000 : 30_000,
  })

  const tabs: { id: Tab; label: string }[] = [
    { id: 'gallery', label: 'Gallery' },
    { id: 'viewer', label: 'Viewer' },
    { id: 'table', label: 'All Photos' },
    { id: 'people', label: 'People' },
  ]

  const markedPhotoIds = getMarkedIds('photo')
  const markedCount = markedPhotoIds.length

  async function markByLevel(level: number) {
    try {
      const { ids } = await api.getPhotoIdsByLevel(level)
      markItems(ids.map((id) => ({ type: 'photo' as const, id })), true)
    } catch (err) {
      console.error('Failed to mark by level', level, err)
    }
  }

  async function markBySync(status: string) {
    try {
      const { ids } = await api.getPhotoIdsBySync(status)
      markItems(ids.map((id) => ({ type: 'photo' as const, id })), true)
    } catch (err) {
      console.error('Failed to mark by sync status', status, err)
    }
  }

  async function markAllUnprocessed() {
    try {
      await Promise.all([0, 1].map(markByLevel))
    } catch (err) {
      console.error('Failed to mark all unprocessed', err)
    }
  }

  const tabAbortRef = useRef<AbortController | null>(null)

  // Extract all clusters from infinite query cache
  function getCachedClusters(): Cluster[] {
    const data = qc.getQueryData<{ pages: ClusterListResult[] }>(['clusters'])
    if (!data?.pages) return []
    return data.pages.flatMap((page) => page.items)
  }

  async function handleTabClick(tabId: Tab) {
    tabAbortRef.current?.abort()
    tabAbortRef.current = new AbortController()

    try {
      const selStore = useSelectionStore.getState()
      const selectedRefs = [...selStore.selected].map(parseItemKey).filter(Boolean) as import('../../store/selection').ItemRef[]
      const clusters = getCachedClusters()
      const findCluster = (id: number) => clusters?.find((c) => c.id === id)

      const lastPhoto = selectedRefs.filter((r) => r.type === 'photo').pop()
      const lastCluster = selectedRefs.filter((r) => r.type === 'cluster').pop()

      if (tabId === 'viewer') {
        if (lastPhoto) { openViewer(lastPhoto.id); return }
        if (lastCluster) {
          if (lastCluster.id < 0) {
            // Search pseudo-cluster: -id is the real photo_id
            openViewer(-lastCluster.id); return
          }
          const cluster = findCluster(lastCluster.id)
          if (cluster?.best_photo_id) { openViewer(cluster.best_photo_id); return }
        }
      }

      if (tabId === 'gallery' && lastPhoto) {
        const { searchQuery, activeClusterId } = useUIStore.getState()
        if (searchQuery) {
          // Search mode: select the pseudo-cluster for this photo (id = -photoId)
          const pseudoId = -lastPhoto.id
          selStore.selectOne({ type: 'cluster', id: pseudoId })
          useUIStore.getState().setActiveCluster(pseudoId, lastPhoto.id)
        } else if (activeClusterId && activeClusterId > 0 && clusters.length) {
          const cluster = findCluster(activeClusterId)
          if (cluster) {
            selStore.selectOne({ type: 'cluster', id: cluster.id })
            useUIStore.getState().setActiveCluster(cluster.id, cluster.best_photo_id ?? null)
          }
        }
      }

      if (tabId === 'table') {
        const clusterRefs = selectedRefs.filter((r) => r.type === 'cluster')
        if (clusterRefs.length > 0) {
          // Split real clusters (id > 0) from search pseudo-clusters (id < 0)
          const realRefs = clusterRefs.filter((r) => r.id > 0)
          const searchRefs = clusterRefs.filter((r) => r.id < 0)

          const photoRefs: Array<{ type: 'photo'; id: number }> = []
          let lastPhotoId: number | null = null
          let lastClusterId: number | null = null

          // Search pseudo-clusters: photo_id = -id
          for (const ref of searchRefs) {
            const pid = -ref.id
            photoRefs.push({ type: 'photo', id: pid })
            lastPhotoId = pid
          }

          // Real clusters: lazy-fetch photo_ids from API
          if (realRefs.length > 0 && clusters.length) {
            try {
              const clusterIds = realRefs.map((r) => r.id)
              const photoIdsMap = await api.getClusterPhotoIds(clusterIds)
              for (const ref of realRefs) {
                const pids = photoIdsMap[ref.id] ?? []
                for (const pid of pids) {
                  photoRefs.push({ type: 'photo', id: pid })
                  lastPhotoId = pid
                }
                if (pids.length > 0) lastClusterId = ref.id
              }
            } catch {
              // Fall through to tab switch
            }
          }

          if (photoRefs.length > 0) {
            selStore.selectMany(photoRefs)
            if (lastPhotoId) useUIStore.getState().setActivePhoto(lastPhotoId, lastClusterId)
          }
        }
      }

      setTab(tabId)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return
      throw e
    }
  }

  return (
    <>
      <div className="flex items-center bg-neutral-900 border-b border-neutral-800 h-11 px-3 gap-4 shrink-0">
        {/* App name */}
        <div className="text-white font-semibold text-sm tracking-tight mr-2">PhotoGal</div>

        {/* Tabs */}
        <div className="flex gap-0.5">
          {tabs.map((t) => (
            <button
              key={t.id}
              className={[
                'px-4 py-1.5 text-sm rounded transition-colors',
                tab === t.id
                  ? 'bg-neutral-700 text-white'
                  : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800',
              ].join(' ')}
              onClick={() => handleTabClick(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Search */}
        <div ref={dropdownRef} className="relative flex items-center">
          <svg className="absolute left-2 w-3.5 h-3.5 text-neutral-500 pointer-events-none z-10" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
          <input
            type="text"
            value={searchInput}
            onChange={(e) => { setSearchInput(e.target.value); setShowDropdown(true) }}
            onFocus={() => setShowDropdown(true)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                if (showDropdown) { setShowDropdown(false) }
                else { setSearchInput(''); (e.target as HTMLInputElement).blur() }
              }
            }}
            placeholder="Search..."
            className="w-48 text-xs bg-neutral-800 border border-neutral-700 rounded pl-7 pr-7 py-1.5 text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-neutral-500 transition-colors"
          />
          {searchInput && (
            <button
              className="absolute right-1.5 text-neutral-500 hover:text-neutral-300 text-xs z-10"
              onClick={() => { setSearchInput(''); setShowDropdown(false) }}
            >
              ✕
            </button>
          )}
          {/* Category dropdown */}
          {showDropdown && hasFilteredCategories && (
            <div role="listbox" className="absolute top-full left-0 mt-1 w-56 max-h-72 overflow-y-auto bg-neutral-800 border border-neutral-700 rounded shadow-lg z-50">
              {filteredCategories.content.length > 0 && (
                <div>
                  <div className="px-2 py-1 text-[10px] text-neutral-500 uppercase tracking-wider">Content</div>
                  {filteredCategories.content.map(({ key, label }) => (
                    <button
                      key={key}
                      role="option"
                      className="w-full text-left px-2 py-1 text-xs text-neutral-200 hover:bg-neutral-700 flex justify-between items-center"
                      onMouseDown={(e) => {
                        e.preventDefault()
                        setSearchInput(key)
                        setShowDropdown(false)
                      }}
                    >
                      <span>{label}</span>
                      <span className="text-neutral-500">{key}</span>
                    </button>
                  ))}
                </div>
              )}
              {filteredCategories.content.length > 0 && filteredCategories.technical.length > 0 && (
                <div className="border-t border-neutral-700" />
              )}
              {filteredCategories.technical.length > 0 && (
                <div>
                  <div className="px-2 py-1 text-[10px] text-neutral-500 uppercase tracking-wider">Technical</div>
                  {filteredCategories.technical.map(({ key, label }) => (
                    <button
                      key={key}
                      role="option"
                      className="w-full text-left px-2 py-1 text-xs text-neutral-200 hover:bg-neutral-700 flex justify-between items-center"
                      onMouseDown={(e) => {
                        e.preventDefault()
                        setSearchInput(key)
                        setShowDropdown(false)
                      }}
                    >
                      <span>{label}</span>
                      <span className="text-neutral-500">{key}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Marked photos actions */}
        {markedCount > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-blue-400 text-xs font-medium px-2 py-1 bg-blue-900/30 rounded">
              {markedCount} marked
            </span>
            <button
              className="px-2 py-1 text-xs text-neutral-400 hover:text-white bg-neutral-800 hover:bg-neutral-700 rounded"
              onClick={clearMarks}
              title="Clear marks"
            >✕</button>
            <button
              className="px-2.5 py-1 text-xs text-blue-300 bg-blue-900/40 hover:bg-blue-800/60 rounded"
              onClick={() => setShowAnalyze(true)}
            >Analyze</button>
            <button
              className="px-2.5 py-1 text-xs text-red-300 bg-red-900/30 hover:bg-red-900/60 rounded"
              onClick={() => setShowDelete(true)}
            >Delete</button>
          </div>
        )}

        {/* Stats counters */}
        {stats && (
          <div className="hidden lg:flex items-center gap-1 text-xs">
            <button
              className="text-neutral-500 hover:text-white hover:bg-neutral-800 px-1.5 py-0.5 rounded transition-colors"
              onClick={markAllUnprocessed}
              title="Mark all unprocessed photos"
            >
              {stats.total_photos.toLocaleString()} total
            </button>
            <button
              className="px-1.5 py-0.5 rounded text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors"
              onClick={() => markByLevel(0)}
              title="Mark level-0 (unscanned) photos"
            >
              Raw:{(stats.by_level[0] ?? 0).toLocaleString()}
            </button>
            <button
              className="px-1.5 py-0.5 rounded text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors"
              onClick={() => markByLevel(1)}
              title="Mark level-1 (quick analysis only) photos"
            >
              L1:{(stats.by_level[1] ?? 0).toLocaleString()}
            </button>
            <button
              className="px-1.5 py-0.5 rounded text-neutral-400 hover:text-white hover:bg-neutral-800 transition-colors"
              onClick={() => markByLevel(2)}
              title="Mark L2 (need face analysis)"
            >
              L2:{(stats.by_level[2] ?? 0).toLocaleString()}
            </button>
            <button
              className="px-1.5 py-0.5 rounded text-green-600 hover:text-green-400 hover:bg-neutral-800 transition-colors"
              onClick={() => markByLevel(3)}
              title="Mark fully analyzed (L3)"
            >
              L3:{(stats.by_level[3] ?? 0).toLocaleString()}
            </button>
            {(stats.disconnected ?? 0) > 0 && (
              <button
                className="px-1.5 py-0.5 rounded text-yellow-400 hover:text-yellow-300 hover:bg-neutral-800 transition-colors"
                onClick={() => markBySync('disconnected')}
                title="Mark disconnected photos"
              >
                ✕:{stats.disconnected.toLocaleString()}
              </button>
            )}
          </div>
        )}

        {/* Zoom (only in gallery) */}
        {tab === 'gallery' && (
          <div className="flex items-center gap-1">
            <button
              className="w-7 h-7 flex items-center justify-center rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-300 text-lg leading-none disabled:opacity-30"
              onClick={zoomOut}
              disabled={zoom === 1}
            >−</button>
            <button
              className="w-7 h-7 flex items-center justify-center rounded bg-neutral-800 hover:bg-neutral-700 text-neutral-300 text-lg leading-none disabled:opacity-30"
              onClick={zoomIn}
              disabled={zoom === 3}
            >+</button>
          </div>
        )}
      </div>

      {showAnalyze && (
        <AnalyzeDialog
          photoIds={markedPhotoIds}
          onClose={() => setShowAnalyze(false)}
          onDone={() => {
            setShowAnalyze(false)
            qc.invalidateQueries({ queryKey: ['stats'] })
          }}
        />
      )}

      {showDelete && (
        <DeleteDialog
          photoIds={markedPhotoIds}
          onClose={() => setShowDelete(false)}
          onDone={() => {
            setShowDelete(false)
            clearMarks()
            qc.invalidateQueries({ queryKey: ['stats'] })
            qc.invalidateQueries({ queryKey: ['photos'] })
            qc.invalidateQueries({ queryKey: ['clusters'] })
          }}
        />
      )}
    </>
  )
}
