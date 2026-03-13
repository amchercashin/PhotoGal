/**
 * All Photos table view.
 * Sortable, filterable, paginated.
 * Double-click / Enter → opens Viewer.
 * Selection and marking same as Gallery.
 *
 * Tab state (page, sort, filter, scroll) is preserved in UIStore.
 * Cross-tab selection: syncs with activePhotoId from UIStore on mount.
 */

import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type Photo } from '../../api/client'
import { useSelectionStore } from '../../store/selection'
import { useUIStore } from '../../store/ui'
import { PhotoThumbnail } from '../shared/PhotoThumbnail'
import { ALL_CATEGORIES } from '../../constants/categories'

const COLUMNS = [
  { key: 'id', label: '#', width: 50 },
  { key: 'thumb', label: '', width: 52, noSort: true },
  { key: 'original_filename', label: 'Filename', width: 200 },
  { key: 'exif_date', label: 'Date', width: 140 },
  { key: 'exif_camera', label: 'Camera', width: 140 },
  { key: 'location_city', label: 'City', width: 100 },
  { key: 'location_country', label: 'Country', width: 80 },
  { key: 'location_district', label: 'District', width: 100, noSort: true },
  { key: 'content_category', label: 'Category', width: 100 },
  { key: 'quality_blur', label: 'Blur', width: 70 },
  { key: 'quality_exposure', label: 'Exp', width: 60 },
  { key: 'quality_aesthetic', label: 'Aes', width: 60 },
  { key: 'processing_level', label: 'Lvl', width: 50 },
  { key: 'exif_gps_lat', label: 'GPS', width: 150, noSort: true },
  { key: 'exif_width', label: 'W×H', width: 90, noSort: true },
  { key: 'file_size', label: 'Size', width: 70 },
  { key: 'cluster_id', label: 'Cluster', width: 70 },
  { key: 'rank_in_cluster', label: 'Rank', width: 55 },
  { key: 'is_exact_duplicate', label: 'Dup', width: 50, noSort: true },
  { key: 'user_decision', label: 'Dec', width: 60 },
  { key: 'sync_status', label: 'Sync', width: 50, noSort: true },
]

const PAGE_SIZE = 100

export function AllPhotosTable() {
  const {
    openViewer,
    tablePage, tableSortBy, tableSortDir, tableFilterCategory,
    setTablePage, setTableSortBy, setTableSortDir, setTableFilterCategory,
  } = useUIStore()
  const setActivePhoto = useUIStore((s) => s.setActivePhoto)
  const setTableScrollOffset = useUIStore((s) => s.setTableScrollOffset)

  const { isSelected, isMarked, selectOne, selectRange, toggleSelect, toggleMarkSelected } = useSelectionStore()
  const isPipelineRunning = useUIStore((s) => s.isPipelineRunning)

  const anchorIdxRef = useRef<number>(0)
  const tbodyRef = useRef<HTMLTableSectionElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  // Track whether mount sync has been attempted
  const mountSyncDoneRef = useRef(false)
  // Photo ID to focus after a page navigation
  const pendingFocusPhotoId = useRef<number | null>(null)

  const { data, isLoading } = useQuery<{ total: number; offset: number; limit: number; items: Photo[] }>({
    queryKey: ['photos-table', tableSortBy, tableSortDir, tablePage, tableFilterCategory],
    queryFn: () =>
      api.getPhotos({
        limit: PAGE_SIZE,
        offset: tablePage * PAGE_SIZE,
        sort_by: tableSortBy,
        sort_dir: tableSortDir,
        filter_category: tableFilterCategory,
      }),
    placeholderData: (prev) => prev,
    refetchInterval: isPipelineRunning ? 5000 : false,
    staleTime: isPipelineRunning ? 0 : 10_000,
  })

  const photos: Photo[] = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const handleSort = (col: string) => {
    if (col === tableSortBy) setTableSortDir(tableSortDir === 'ASC' ? 'DESC' : 'ASC')
    else { setTableSortBy(col); setTableSortDir('ASC') }
    setTablePage(0)
  }

  // Save scroll position to store on every scroll
  useEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return
    const handler = () => setTableScrollOffset(el.scrollTop)
    el.addEventListener('scroll', handler, { passive: true })
    return () => el.removeEventListener('scroll', handler)
  }, [setTableScrollOffset])

  // Cross-tab sync + restore scroll on mount.
  // Runs every time photos change but uses refs to only act once per mount.
  useEffect(() => {
    if (photos.length === 0) return

    // Case 1: pending focus after page navigation
    if (pendingFocusPhotoId.current !== null) {
      const targetId = pendingFocusPhotoId.current
      const el = tbodyRef.current?.querySelector(`[data-photo-id="${targetId}"]`) as HTMLElement | null
      if (el) {
        pendingFocusPhotoId.current = null
        el.focus()
      }
      return
    }

    // Case 2: mount sync (only once after first data load)
    if (mountSyncDoneRef.current) return
    mountSyncDoneRef.current = true

    const { activePhotoId, tableScrollOffset } = useUIStore.getState()
    if (activePhotoId) {
      // Check if the photo is on the current page
      const el = tbodyRef.current?.querySelector(`[data-photo-id="${activePhotoId}"]`) as HTMLElement | null
      if (el) {
        el.focus()
      } else {
        // Navigate to the page containing the photo
        api.getPhotoTablePosition(activePhotoId, {
          sort_by: tableSortBy,
          sort_dir: tableSortDir,
          filter_category: tableFilterCategory,
          page_size: PAGE_SIZE,
        }).then(({ page: targetPage, found }) => {
          if (found && targetPage !== tablePage) {
            pendingFocusPhotoId.current = activePhotoId
            setTablePage(targetPage)
          }
        }).catch(() => {/* best-effort */})
      }
    } else if (tableScrollOffset > 0) {
      // No active photo — restore last scroll position
      if (scrollContainerRef.current) {
        scrollContainerRef.current.scrollTop = tableScrollOffset
      }
    }
  }, [photos]) // eslint-disable-line react-hooks/exhaustive-deps
  // Intentionally minimal deps: mount sync runs once, page-nav is driven by pendingFocusPhotoId ref

  const handleKeyDown = (e: React.KeyboardEvent, photo: Photo, idx: number) => {
    if (e.key === 'Enter') openViewer(photo.id)
    if (e.key === ' ') { e.preventDefault(); toggleMarkSelected() }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (idx < photos.length - 1) {
        const nextIdx = idx + 1
        const next = photos[nextIdx]
        if (e.shiftKey) {
          selectRange(photos.map((p) => ({ type: 'photo' as const, id: p.id })), anchorIdxRef.current, nextIdx)
        } else {
          selectOne({ type: 'photo', id: next.id })
          anchorIdxRef.current = nextIdx
        }
        setActivePhoto(next.id, next.cluster_id ?? null)
        ;(e.currentTarget.nextElementSibling as HTMLElement)?.focus()
      }
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (idx > 0) {
        const prevIdx = idx - 1
        const prev = photos[prevIdx]
        if (e.shiftKey) {
          selectRange(photos.map((p) => ({ type: 'photo' as const, id: p.id })), anchorIdxRef.current, prevIdx)
        } else {
          selectOne({ type: 'photo', id: prev.id })
          anchorIdxRef.current = prevIdx
        }
        setActivePhoto(prev.id, prev.cluster_id ?? null)
        ;(e.currentTarget.previousElementSibling as HTMLElement)?.focus()
      }
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex gap-3 px-4 py-2 bg-neutral-900 border-b border-neutral-800 shrink-0">
        <span className="text-neutral-400 text-sm self-center">{total} photos</span>
        <select
          className="bg-neutral-800 text-neutral-200 text-sm px-2 py-1 rounded border border-neutral-700"
          value={tableFilterCategory ?? ''}
          onChange={(e) => { setTableFilterCategory(e.target.value || undefined); setTablePage(0) }}
        >
          <option value="">All categories</option>
          {ALL_CATEGORIES.map(({ key, label }) => (
            <option key={key} value={key}>{label} ({key})</option>
          ))}
        </select>
      </div>

      {/* Table */}
      <div ref={scrollContainerRef} className="flex-1 overflow-auto">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-neutral-900 z-10">
            <tr>
              {COLUMNS.map((col) => (
                <th
                  key={col.key}
                  style={{ width: col.width, minWidth: col.width }}
                  className={[
                    'text-left px-2 py-2 text-neutral-400 font-medium border-b border-neutral-800 select-none',
                    !col.noSort ? 'cursor-pointer hover:text-neutral-200' : '',
                  ].join(' ')}
                  onClick={() => !col.noSort && handleSort(col.key)}
                >
                  {col.label}
                  {tableSortBy === col.key && (
                    <span className="ml-1 text-blue-400">{tableSortDir === 'ASC' ? '↑' : '↓'}</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody ref={tbodyRef}>
            {isLoading && (
              <tr><td colSpan={COLUMNS.length} className="text-center py-8 text-neutral-500">Loading...</td></tr>
            )}
            {photos.map((photo, idx) => {
              const ref = { type: 'photo' as const, id: photo.id }
              const selected = isSelected(ref)
              const marked = isMarked(ref)
              return (
                <tr
                  key={photo.id}
                  tabIndex={0}
                  data-photo-id={photo.id}
                  className={[
                    'cursor-pointer border-b border-neutral-800/50 transition-colors',
                    selected ? 'ring-2 ring-blue-400 ring-inset' : 'hover:bg-neutral-800/50',
                    marked ? 'opacity-60' : '',
                  ].join(' ')}
                  onClick={(e) => {
                    if (e.shiftKey) {
                      selectRange(photos.map((p) => ({ type: 'photo' as const, id: p.id })), anchorIdxRef.current, idx)
                    } else if (e.metaKey || e.ctrlKey) {
                      toggleSelect(ref)
                      anchorIdxRef.current = idx
                    } else {
                      selectOne(ref)
                      anchorIdxRef.current = idx
                      setActivePhoto(photo.id, photo.cluster_id ?? null)
                    }
                  }}
                  onFocus={(e) => {
                    selectOne({ type: 'photo', id: photo.id })
                    anchorIdxRef.current = idx
                    setActivePhoto(photo.id, photo.cluster_id ?? null)
                    e.currentTarget.scrollIntoView({ block: 'nearest' })
                  }}
                  onDoubleClick={() => openViewer(photo.id)}
                  onKeyDown={(e) => handleKeyDown(e, photo, idx)}
                >
                  <td className="px-2 py-1 text-neutral-500">{photo.id}</td>
                  <td className="px-1 py-0.5">
                    <div className="relative w-10 h-10 rounded overflow-hidden">
                      <PhotoThumbnail photoId={photo.id} version={photo.content_hash} className="w-full h-full object-cover" />
                      {marked && (
                        <div className="absolute inset-0 bg-blue-500/80 flex items-center justify-center pointer-events-none">
                          <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        </div>
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-1 truncate max-w-0" style={{ maxWidth: 200 }}>{photo.original_filename}</td>
                  <td className="px-2 py-1 text-neutral-400 whitespace-nowrap">{photo.exif_date?.slice(0, 10)}</td>
                  <td className="px-2 py-1 text-neutral-400 truncate" style={{ maxWidth: 140 }}>{photo.exif_camera}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.location_city}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.location_country}</td>
                  <td className="px-2 py-1 text-neutral-400 max-w-[100px] truncate">{photo.location_district ?? '—'}</td>
                  <td className="px-2 py-1">
                    {photo.content_category && (
                      <span className="text-xs bg-neutral-700 rounded px-1.5 py-0.5">{photo.content_category}</span>
                    )}
                  </td>
                  <td className="px-2 py-1 text-neutral-400">{photo.quality_blur?.toFixed(0)}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.quality_exposure?.toFixed(0)}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.quality_aesthetic?.toFixed(1)}</td>
                  <td className="px-2 py-1 text-center">
                    <span className="text-xs bg-neutral-700 rounded px-1 py-0.5">{photo.processing_level}</span>
                  </td>
                  <td className="px-2 py-1 whitespace-nowrap text-neutral-400">{photo.exif_gps_lat != null ? `${photo.exif_gps_lat.toFixed(4)}, ${photo.exif_gps_lon!.toFixed(4)}` : '—'}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.exif_width && photo.exif_height ? `${photo.exif_width}×${photo.exif_height}` : '—'}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.file_size ? `${(photo.file_size / 1_048_576).toFixed(1)} MB` : '—'}</td>
                  <td className="px-2 py-1 text-neutral-500">{photo.cluster_id ?? '—'}</td>
                  <td className="px-2 py-1 text-neutral-400">{photo.rank_in_cluster ?? '—'}</td>
                  <td className="px-2 py-1">{photo.is_exact_duplicate ? <span className="text-[10px] bg-orange-700/60 px-1 rounded">dup</span> : ''}</td>
                  <td className="px-2 py-1">{photo.user_decision ? <span className="text-[10px] bg-neutral-600 px-1 rounded">{photo.user_decision}</span> : ''}</td>
                  <td className="px-2 py-1">{photo.sync_status === 'disconnected' ? <span className="text-yellow-500">✕</span> : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 py-2 bg-neutral-900 border-t border-neutral-800 shrink-0">
          <button
            className="px-3 py-1 text-sm bg-neutral-800 rounded disabled:opacity-30"
            disabled={tablePage === 0}
            onClick={() => setTablePage(tablePage - 1)}
          >Prev</button>
          <span className="text-neutral-400 text-sm">{tablePage + 1} / {totalPages}</span>
          <button
            className="px-3 py-1 text-sm bg-neutral-800 rounded disabled:opacity-30"
            disabled={tablePage >= totalPages - 1}
            onClick={() => setTablePage(tablePage + 1)}
          >Next</button>
        </div>
      )}
    </div>
  )
}
