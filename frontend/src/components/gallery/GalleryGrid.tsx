/**
 * Main Gallery grid.
 *
 * - Server-side pagination via useInfiniteQuery (loads pages of clusters)
 * - Virtualized rows via TanStack Virtual (only visible rows rendered)
 * - Adaptive column count based on zoom level + container width
 * - Keyboard navigation (arrow keys, Enter, Space)
 * - Shift+Arrow -> range selection
 * - Click / Shift+click / Ctrl+click selection
 * - Space -> toggle mark cluster (photo-centric, lazy-fetches photo_ids)
 * - Double click -> open Viewer
 * - Scroll-to-selected: on mount and after keyboard navigation
 * - Per-tab scroll state preserved in UIStore
 */

import { useEffect, useLayoutEffect, useRef, useCallback, useState, useMemo } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
// pipeline-status is polled by usePipelineSync (App.tsx) → isPipelineRunning lives in UIStore
import { api, type Cluster } from '../../api/client'
import { useSelectionStore, parseItemKey, type ItemRef } from '../../store/selection'
import { useUIStore } from '../../store/ui'
import { ClusterCell } from './ClusterCell'

const PADDING = 12 // p-3 = 12px on each side
const GAP = 4      // gap-1 = 4px
const PAGE_SIZE = 200

export function GalleryGrid() {
  const { zoom, zoomIn, zoomOut, openViewer } = useUIStore()
  const searchQuery = useUIStore((s) => s.searchQuery)
  const setActiveCluster = useUIStore((s) => s.setActiveCluster)
  const setActivePhoto = useUIStore((s) => s.setActivePhoto)
  const setGalleryScrollOffset = useUIStore((s) => s.setGalleryScrollOffset)
  const {
    isSelected, isClusterMarked, selectOne, selectRange, toggleSelect, toggleMarkCluster,
  } = useSelectionStore()
  const isPipelineRunning = useUIStore((s) => s.isPipelineRunning)
  const isSearchMode = searchQuery.length > 0

  // Search query
  const { data: searchData, isLoading: isSearchLoading } = useQuery({
    queryKey: ['search', searchQuery],
    queryFn: () => api.searchPhotos(searchQuery),
    enabled: isSearchMode,
    staleTime: 30_000,
  })

  // Convert search results to pseudo-clusters for rendering
  const searchClusters = useMemo(() => {
    if (!searchData?.results) return []
    return searchData.results.map((r) => ({
      id: -r.photo_id,
      name: null,
      best_photo_id: r.photo_id,
      photo_count: 1,
      type: 'singleton' as const,
      avg_timestamp: null,
      avg_gps_lat: null,
      avg_gps_lon: null,
      location_city: null,
      best_photo_blur: null,
      best_photo_exposure: null,
      has_exact_duplicate: false,
      _similarity: r.similarity,
    }))
  }, [searchData])

  type SearchCluster = Cluster & { _similarity?: number }

  const gridRef = useRef<HTMLDivElement>(null)
  const cursorIdxRef = useRef<number>(0)
  const anchorIdxRef = useRef<number>(0)
  const colsRef = useRef<number>(5)
  // Prevents repeated scroll-to-selected after first mount sync
  const initialScrollDoneRef = useRef(false)

  const [cols, setCols] = useState(5)
  const [containerWidth, setContainerWidth] = useState(0)

  // Photo IDs cache — lazily fetched per cluster (ref is source of truth, counter triggers re-renders)
  const photoIdsCacheRef = useRef<Record<number, number[]>>({})
  const [, setCacheVersion] = useState(0)

  const {
    data: clusterPages,
    isLoading,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['clusters'],
    queryFn: ({ pageParam = 0 }) =>
      api.getClusters({ nonempty: true, limit: PAGE_SIZE, offset: pageParam }),
    getNextPageParam: (lastPage) => {
      const nextOffset = lastPage.offset + lastPage.limit
      return nextOffset < lastPage.total ? nextOffset : undefined
    },
    initialPageParam: 0,
    refetchInterval: isPipelineRunning ? 3000 : 30000,
    staleTime: isPipelineRunning ? 0 : 10_000,
  })

  // Flatten all pages into items, sort by avg_timestamp (nulls last)
  const clusterItems: SearchCluster[] = useMemo(
    () =>
      (clusterPages?.pages ?? [])
        .flatMap((page) => page.items)
        .sort((a, b) => {
          if (!a.avg_timestamp) return 1
          if (!b.avg_timestamp) return -1
          return a.avg_timestamp.localeCompare(b.avg_timestamp)
        }),
    [clusterPages],
  )

  const items: SearchCluster[] = isSearchMode ? searchClusters : clusterItems
  const totalClusters = isSearchMode ? searchClusters.length : (clusterPages?.pages[0]?.total ?? 0)
  const effectiveLoading = isSearchMode ? isSearchLoading : isLoading

  // Lazy-fetch photo_ids for clusters (stable ref — reads cache via ref)
  const fetchPhotoIds = useCallback(async (clusterIds: number[]) => {
    const cache = photoIdsCacheRef.current
    const needed = clusterIds.filter((id) => !(id in cache))
    if (needed.length === 0) {
      return clusterIds.flatMap((id) => cache[id] ?? [])
    }
    const result = await api.getClusterPhotoIds(needed)
    const merged = { ...cache, ...result }
    photoIdsCacheRef.current = merged
    setCacheVersion(v => v + 1)
    const all: number[] = []
    for (const id of clusterIds) {
      all.push(...(merged[id] ?? []))
    }
    return all
  }, [])

  const refFor = useCallback((c: Cluster): ItemRef => ({ type: 'cluster', id: c.id }), [])

  const minCellSize = ({ 1: 120, 2: 200, 3: 300 } as Record<number, number>)[zoom] ?? 200

  const measureContainer = useCallback(() => {
    const el = gridRef.current
    if (!el) return
    const w = el.clientWidth
    setContainerWidth(w)
    const innerWidth = w - 2 * PADDING
    const count = Math.max(1, Math.floor((innerWidth + GAP) / (minCellSize + GAP)))
    colsRef.current = count
    setCols(count)
  }, [minCellSize])

  // Measure synchronously before first paint to avoid layout glitch
  useLayoutEffect(() => {
    measureContainer()
  }, [measureContainer])

  // Keep in sync on resize and zoom changes
  useEffect(() => {
    const el = gridRef.current
    if (!el) return
    const ro = new ResizeObserver(measureContainer)
    ro.observe(el)
    return () => ro.disconnect()
  }, [measureContainer])

  // Focus grid on mount (e.g. when switching back from viewer)
  useEffect(() => {
    gridRef.current?.focus()
  }, [])

  // Save scroll position to store on every scroll (passive, no re-renders)
  useEffect(() => {
    const el = gridRef.current
    if (!el) return
    const handler = () => setGalleryScrollOffset(el.scrollTop)
    el.addEventListener('scroll', handler, { passive: true })
    return () => el.removeEventListener('scroll', handler)
  }, [setGalleryScrollOffset])

  // Compute cell height from actual available width; fall back to minCellSize until first measurement
  const innerWidth = Math.max(0, containerWidth - 2 * PADDING)
  const cellHeight = innerWidth > 0 && cols > 0
    ? (innerWidth - (cols - 1) * GAP) / cols
    : minCellSize

  const rowCount = Math.ceil(items.length / cols)

  const virtualizer = useVirtualizer({
    count: rowCount,
    getScrollElement: () => gridRef.current,
    estimateSize: () => Math.round(cellHeight),
    overscan: 3,
    paddingStart: PADDING,
    paddingEnd: PADDING,
    gap: GAP,
  })

  // Auto-load next page when scrolling near the bottom
  const virtualItems = virtualizer.getVirtualItems()
  const lastVisibleRow = virtualItems.length > 0 ? virtualItems[virtualItems.length - 1].index : -1
  useEffect(() => {
    if (!hasNextPage || isFetchingNextPage) return
    if (lastVisibleRow >= rowCount - 3) {
      fetchNextPage()
    }
  }, [lastVisibleRow, hasNextPage, isFetchingNextPage, rowCount, fetchNextPage])

  // On mount (after items and cols are ready): scroll to activeClusterId or restore scroll offset.
  // Only runs once - subsequent selections during this mount are handled by moveCursor/onSelect.
  useEffect(() => {
    if (initialScrollDoneRef.current) return
    if (!items.length || !cols) return

    initialScrollDoneRef.current = true

    const { activeClusterId, galleryScrollOffset } = useUIStore.getState()
    if (activeClusterId) {
      const idx = items.findIndex((c) => c.id === activeClusterId)
      if (idx >= 0) {
        cursorIdxRef.current = idx
        anchorIdxRef.current = idx
        const rowIdx = Math.floor(idx / cols)
        virtualizer.scrollToIndex(rowIdx, { align: 'auto' })
        return
      }
    }

    // No active selection - restore last scroll position
    if (galleryScrollOffset > 0 && gridRef.current) {
      gridRef.current.scrollTop = galleryScrollOffset
    }
  }, [items, cols]) // eslint-disable-line react-hooks/exhaustive-deps
  // Intentionally omitting activeClusterId and galleryScrollOffset from deps:
  // we only want to run this once after first data load, not on every re-render.

  const moveCursor = useCallback(
    (delta: number, extendSelection = false) => {
      if (!items.length) return
      const next = Math.max(0, Math.min(items.length - 1, cursorIdxRef.current + delta))
      cursorIdxRef.current = next
      const item = items[next]
      if (extendSelection) {
        selectRange(items.map(refFor), anchorIdxRef.current, next)
      } else {
        anchorIdxRef.current = next
        selectOne(refFor(items[next]))
        if (item) {
          if (isSearchMode) {
            if (item.best_photo_id != null) setActivePhoto(item.best_photo_id, null)
          } else {
            setActiveCluster(item.id, item.best_photo_id ?? null)
          }
        }
      }
      // Ensure the cursor row is visible
      const rowIdx = Math.floor(next / colsRef.current)
      virtualizer.scrollToIndex(rowIdx, { align: 'auto' })
    },
    [items, selectOne, selectRange, refFor, virtualizer, setActiveCluster, isSearchMode, setActivePhoto],
  )

  useEffect(() => {
    const el = gridRef.current
    if (!el) return

    const handler = (e: KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowRight': e.preventDefault(); moveCursor(1, e.shiftKey); break
        case 'ArrowLeft':  e.preventDefault(); moveCursor(-1, e.shiftKey); break
        case 'ArrowDown':  e.preventDefault(); moveCursor(colsRef.current, e.shiftKey); break
        case 'ArrowUp':    e.preventDefault(); moveCursor(-colsRef.current, e.shiftKey); break
        case ' ': {
          e.preventDefault()
          if (isSearchMode) {
            // Search mode: resolve photo IDs directly from items (no API call needed)
            const { selected } = useSelectionStore.getState()
            const photoIds: number[] = []
            if (selected.size > 0) {
              for (const key of selected) {
                const ref = parseItemKey(key)
                if (ref?.type === 'cluster') {
                  const item = items.find((c) => c.id === ref.id)
                  if (item?.best_photo_id) photoIds.push(item.best_photo_id)
                }
              }
            }
            if (photoIds.length === 0) {
              const cur = items[cursorIdxRef.current]
              if (cur?.best_photo_id) photoIds.push(cur.best_photo_id)
            }
            if (photoIds.length > 0) toggleMarkCluster(photoIds)
          } else {
            // Normal mode: lazy-fetch photo_ids from API
            const { selected } = useSelectionStore.getState()
            const clusterIds: number[] = []
            for (const key of selected) {
              const ref = parseItemKey(key)
              if (ref?.type === 'cluster') clusterIds.push(ref.id)
            }
            if (clusterIds.length === 0) {
              const cur = items[cursorIdxRef.current]
              if (cur) clusterIds.push(cur.id)
            }
            if (clusterIds.length > 0) {
              fetchPhotoIds(clusterIds).then((ids) => {
                if (ids.length > 0) toggleMarkCluster(ids)
              })
            }
          }
          break
        }
        case 'Enter': {
          e.preventDefault()
          e.stopPropagation()
          const cur = items[cursorIdxRef.current]
          if (cur?.best_photo_id) openViewer(cur.best_photo_id)
          break
        }
        case 'Home': e.preventDefault(); moveCursor(-cursorIdxRef.current, false); break
        case 'End': e.preventDefault(); moveCursor(items.length - 1 - cursorIdxRef.current, false); break
        case 'Escape': e.preventDefault(); useSelectionStore.getState().clearSelection(); break
        case '+':
        case '=':
          if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomIn() }
          break
        case '-':
          if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomOut() }
          break
      }
    }

    el.addEventListener('keydown', handler)
    return () => el.removeEventListener('keydown', handler)
  }, [moveCursor, toggleMarkCluster, openViewer, zoomIn, zoomOut, items, fetchPhotoIds, isSearchMode])

  // isClusterMarked with cache: check cached photo_ids, fallback to best_photo_id
  const checkMarked = useCallback((cluster: Cluster): boolean => {
    // Search pseudo-clusters have negative IDs — use best_photo_id directly
    if (cluster.id < 0 && cluster.best_photo_id) {
      return isClusterMarked([cluster.best_photo_id])
    }
    const ids = cluster.photo_ids ?? photoIdsCacheRef.current[cluster.id]
    if (ids && ids.length > 0) return isClusterMarked(ids)
    // Fallback: photo_ids not yet fetched — check best_photo_id
    if (cluster.best_photo_id) return isClusterMarked([cluster.best_photo_id])
    return false
  }, [isClusterMarked])

  // Always render the scroll container so gridRef is mounted immediately,
  // allowing useLayoutEffect to measure width before data arrives.
  return (
    <div
      ref={gridRef}
      tabIndex={0}
      role="grid"
      aria-label="Photo gallery"
      className="outline-none w-full h-full overflow-y-auto"
      style={{ scrollbarGutter: 'stable' }}
    >
      {effectiveLoading && (
        <div className="flex items-center justify-center h-full text-neutral-500">
          Loading...
        </div>
      )}

      {!effectiveLoading && error && !isSearchMode && (
        <div className="flex items-center justify-center h-full text-red-400">
          Failed to load gallery
        </div>
      )}

      {/* Search mode header */}
      {isSearchMode && !isSearchLoading && (
        <div className="px-3 pt-3 pb-1 text-xs text-neutral-400">
          {searchData && searchData.results.length > 0 ? (
            <span>
              {searchData.results.length} results for &lsquo;{searchData.query}&rsquo;
              {searchData.translated_query && (
                <span className="text-neutral-500 ml-1">
                  → {searchData.translated_query}
                </span>
              )}
              <span className="text-neutral-600 ml-2">{searchData.elapsed_ms.toFixed(0)}ms</span>
              {searchData.total_with_embeddings === 0 && (
                <span className="text-yellow-500 ml-2">Run L2 analysis to enable search</span>
              )}
            </span>
          ) : searchData?.total_with_embeddings === 0 ? (
            <span className="text-yellow-500">No L2 embeddings yet — run full analysis (L2) first</span>
          ) : (
            <span>
              No results for &lsquo;{searchQuery}&rsquo;
              {searchData?.translated_query && (
                <span className="text-neutral-500 ml-1">
                  → {searchData.translated_query}
                </span>
              )}
            </span>
          )}
        </div>
      )}

      {!effectiveLoading && !error && !items.length && !isSearchMode && (
        <div className="flex flex-col items-center justify-center h-full text-neutral-500 gap-3">
          <svg className="w-16 h-16 opacity-30" fill="currentColor" viewBox="0 0 24 24">
            <path d="M19 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2zM5 5h14v9l-3-3-4 4-3-3-4 3V5z"/>
          </svg>
          <p className="text-lg">No photos yet</p>
          <p className="text-sm">Add a source folder to get started</p>
        </div>
      )}

      {items.length > 0 && (
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const rowStart = virtualRow.index * cols
          const rowItems = items.slice(rowStart, rowStart + cols)

          return (
            <div
              key={virtualRow.key}
              style={{
                position: 'absolute',
                top: `${virtualRow.start}px`,
                left: `${PADDING}px`,
                right: `${PADDING}px`,
                height: `${virtualRow.size}px`,
                display: 'grid',
                gridTemplateColumns: `repeat(${cols}, 1fr)`,
                gap: `${GAP}px`,
              }}
            >
              {rowItems.map((cluster, colIdx) => {
                const idx = rowStart + colIdx
                const ref = refFor(cluster)
                return (
                  <ClusterCell
                    key={cluster.id}
                    cluster={cluster}
                    isSelected={isSelected(ref)}
                    isMarked={checkMarked(cluster)}
                    similarity={(cluster as SearchCluster)._similarity}
                    onSelect={(e) => {
                      gridRef.current?.focus()
                      cursorIdxRef.current = idx
                      if (e.shiftKey && items.length) {
                        selectRange(items.map(refFor), anchorIdxRef.current, idx)
                      } else if (e.metaKey || e.ctrlKey) {
                        toggleSelect(ref)
                        anchorIdxRef.current = idx
                      } else {
                        anchorIdxRef.current = idx
                        selectOne(ref)
                        if (isSearchMode) {
                          if (cluster.best_photo_id != null) setActivePhoto(cluster.best_photo_id, null)
                        } else {
                          setActiveCluster(cluster.id, cluster.best_photo_id ?? null)
                        }
                      }
                    }}
                    onDoubleClick={() => {
                      if (cluster.best_photo_id) openViewer(cluster.best_photo_id)
                    }}
                  />
                )
              })}
            </div>
          )
        })}
      </div>
      )}

      {/* Load more indicator (cluster mode only) */}
      {!isSearchMode && isFetchingNextPage && (
        <div className="flex items-center justify-center py-4 text-neutral-500 text-sm">
          Loading more...
        </div>
      )}

      {/* Total count (cluster mode only) */}
      {!isSearchMode && totalClusters > 0 && items.length < totalClusters && hasNextPage && (
        <div className="flex items-center justify-center py-2 text-neutral-600 text-xs">
          {items.length} / {totalClusters}
        </div>
      )}
    </div>
  )
}
