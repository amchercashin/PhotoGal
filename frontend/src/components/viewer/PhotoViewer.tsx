/**
 * Photo Viewer tab.
 *
 * - Large main photo display
 * - Cluster filmstrip below
 * - Metadata panel on right
 * - Fullscreen mode (double-click or Enter)
 * - Zoom in fullscreen (scroll wheel, +/-)
 * - Keyboard: arrows navigate, Space marks, Escape exits fullscreen
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type Photo } from '../../api/client'
import { useSelectionStore } from '../../store/selection'
import { useUIStore } from '../../store/ui'
import { PhotoThumbnail } from '../shared/PhotoThumbnail'
import { FaceOverlay } from '../gallery/FaceOverlay'

export function PhotoViewer() {
  const { tab, viewerPhotoId, openViewer, closeViewer } = useUIStore()
  const { isMarked, selectOne, toggleMarkSelected } = useSelectionStore()

  const [fullscreen, setFullscreen] = useState(false)
  const [showFaces, setShowFaces] = useState(false)

  const { data: photo } = useQuery({
    queryKey: ['photo', viewerPhotoId],
    queryFn: () => api.getPhoto(viewerPhotoId!),
    enabled: viewerPhotoId != null,
  })

  const { data: cluster } = useQuery({
    queryKey: ['cluster', photo?.cluster_id],
    queryFn: () => api.getCluster(photo!.cluster_id!),
    enabled: photo?.cluster_id != null,
  })

  const clusterPhotos: Photo[] = cluster?.photos ?? (photo ? [photo] : [])
  const currentIdx = clusterPhotos.findIndex((p) => p.id === viewerPhotoId)

  const navigate = useCallback(
    (delta: number) => {
      const next = currentIdx + delta
      if (next >= 0 && next < clusterPhotos.length) {
        const p = clusterPhotos[next]
        openViewer(p.id)
        selectOne({ type: 'photo', id: p.id })
      }
    },
    [currentIdx, clusterPhotos, openViewer, selectOne],
  )

  // Reset fullscreen when opening a different photo
  useEffect(() => {
    setFullscreen(false)
  }, [viewerPhotoId])

  const exitFullscreen = useCallback(() => setFullscreen(false), [])
  const enterFullscreen = useCallback(() => setFullscreen(true), [])

  // Keyboard
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Only handle keys when Viewer is the active tab (except Escape to close fullscreen)
      if (tab !== 'viewer') {
        if (e.key === 'Escape' && fullscreen) exitFullscreen()
        return
      }
      switch (e.key) {
        case 'Escape':
          if (fullscreen) exitFullscreen()
          else closeViewer()
          break
        case 'ArrowRight': e.preventDefault(); navigate(1); break
        case 'ArrowLeft':  e.preventDefault(); navigate(-1); break
        case ' ':
          e.preventDefault()
          if (viewerPhotoId) {
            selectOne({ type: 'photo', id: viewerPhotoId })
            toggleMarkSelected()
          }
          break
        case 'Enter': if (!fullscreen) enterFullscreen(); break
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [tab, fullscreen, navigate, closeViewer, exitFullscreen, enterFullscreen, viewerPhotoId, selectOne, toggleMarkSelected])

  if (!photo) {
    return (
      <div className="flex items-center justify-center h-full text-neutral-500">
        Select a photo to view
      </div>
    )
  }

  const marked = isMarked({ type: 'photo', id: photo.id })
  const imgUrl = api.fullUrl(photo.id)
  const orientDeg = _orientDeg(photo.exif_orientation)

  return (
    <>
      {/* Main viewer layout */}
      <div className="flex h-full overflow-hidden">
        {/* Center: main image */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div
            className="flex-1 relative flex items-center justify-center bg-black overflow-hidden cursor-zoom-in"
            onDoubleClick={enterFullscreen}
            onMouseEnter={() => setShowFaces(true)}
            onMouseLeave={() => setShowFaces(false)}
          >
            <img
              src={imgUrl}
              alt={photo.original_filename}
              className="max-w-full max-h-full object-contain select-none"
              style={{ transform: `rotate(${orientDeg}deg)` }}
              draggable={false}
            />
            <FaceOverlay photoId={viewerPhotoId!} visible={showFaces} />

            {/* Nav arrows */}
            {currentIdx > 0 && (
              <button
                className="absolute left-2 top-1/2 -translate-y-1/2 bg-black/50 hover:bg-black/80 text-white rounded-full w-9 h-9 flex items-center justify-center"
                onClick={(e) => { e.stopPropagation(); navigate(-1) }}
              >‹</button>
            )}
            {currentIdx < clusterPhotos.length - 1 && (
              <button
                className="absolute right-2 top-1/2 -translate-y-1/2 bg-black/50 hover:bg-black/80 text-white rounded-full w-9 h-9 flex items-center justify-center"
                onClick={(e) => { e.stopPropagation(); navigate(1) }}
              >›</button>
            )}

            {/* Fullscreen button */}
            <button
              className="absolute top-2 right-2 bg-black/40 hover:bg-black/70 text-white rounded p-1.5"
              onClick={(e) => { e.stopPropagation(); enterFullscreen() }}
              title="Fullscreen (Enter)"
            >
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                <path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/>
              </svg>
            </button>

            {/* Marked indicator */}
            {marked && (
              <div className="absolute top-2 left-2 bg-blue-500 rounded-full p-1">
                <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                </svg>
              </div>
            )}
          </div>

          {/* Filmstrip */}
          {clusterPhotos.length > 1 && (
            <div className="h-20 bg-neutral-900 flex gap-1 px-2 py-1 overflow-x-auto shrink-0">
              {clusterPhotos.map((p) => {
                const filmMarked = isMarked({ type: 'photo', id: p.id })
                return (
                  <button
                    key={p.id}
                    onClick={() => { openViewer(p.id); selectOne({ type: 'photo', id: p.id }) }}
                    className={[
                      'relative shrink-0 rounded overflow-hidden',
                      p.id === viewerPhotoId ? 'ring-2 ring-blue-400' : 'opacity-60 hover:opacity-100',
                    ].join(' ')}
                    style={{ width: 72, height: '100%' }}
                  >
                    <PhotoThumbnail
                      photoId={p.id}
                      className="w-full h-full object-cover"
                    />
                    {filmMarked && (
                      <div className="absolute inset-0 bg-blue-500/70 flex items-center justify-center pointer-events-none">
                        <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Right: metadata panel */}
        <div className="w-72 shrink-0 bg-neutral-900 border-l border-neutral-800 overflow-y-auto p-4 text-sm">
          <MetadataPanel photo={photo} />
        </div>
      </div>

      {/* Fullscreen overlay */}
      {fullscreen && (
        <FullscreenOverlay
          imgUrl={imgUrl}
          alt={photo.original_filename}
          orientDeg={orientDeg}
          onClose={exitFullscreen}
          onNavigate={navigate}
          hasPrev={currentIdx > 0}
          hasNext={currentIdx < clusterPhotos.length - 1}
        />
      )}
    </>
  )
}

function MetadataPanel({ photo }: { photo: Photo }) {
  const rows: [string, string | number | null | undefined][] = [
    ['File', photo.original_filename],
    ['Date', photo.exif_date?.replace('T', ' ')],
    ['Camera', photo.exif_camera],
    ['Size', photo.file_size ? `${(photo.file_size / 1024 / 1024).toFixed(1)} MB` : null],
    ['Dimensions', photo.exif_width && photo.exif_height ? `${photo.exif_width} × ${photo.exif_height}` : null],
    ['Country', photo.location_country],
    ['City', photo.location_city],
    ['District', photo.location_district],
    ['Blur', photo.quality_blur?.toFixed(0)],
    ['Exposure', photo.quality_exposure?.toFixed(0)],
    ['Aesthetic', photo.quality_aesthetic?.toFixed(2)],
    ['Category', photo.content_category],
    ['Technical', photo.is_technical ? 'Yes' : null],
    ['Rank in cluster', photo.rank_in_cluster],
    ['Level', `${photo.processing_level}`],
  ]

  return (
    <div className="space-y-0.5">
      <h3 className="text-neutral-400 text-xs font-semibold uppercase tracking-wider mb-3">Metadata</h3>
      {rows.map(([label, value]) =>
        value != null ? (
          <div key={label} className="flex gap-2">
            <span className="text-neutral-500 shrink-0 w-24">{label}</span>
            <span className="text-neutral-200 break-all">{String(value)}</span>
          </div>
        ) : null,
      )}
      {photo.exif_gps_lat && photo.exif_gps_lon && (
        <div className="mt-2">
          <span className="text-neutral-500">GPS </span>
          <span className="text-neutral-200 text-xs">
            {photo.exif_gps_lat.toFixed(5)}, {photo.exif_gps_lon.toFixed(5)}
          </span>
        </div>
      )}
    </div>
  )
}

function FullscreenOverlay({
  imgUrl, alt, orientDeg, onClose, onNavigate, hasPrev, hasNext,
}: {
  imgUrl: string; alt: string; orientDeg: number
  onClose: () => void; onNavigate: (d: number) => void
  hasPrev: boolean; hasNext: boolean
}) {
  const [zoom, setZoom] = useState(1.0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const isDragging = useRef(false)
  const [isDraggingStyle, setIsDraggingStyle] = useState(false)
  const dragStart = useRef({ x: 0, y: 0, px: 0, py: 0 })

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(z + 0.25, 4))
      if (e.key === '-') setZoom((z) => Math.max(z - 0.25, 1.0))
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    setZoom((z) => Math.max(1.0, Math.min(4, z - e.deltaY * 0.001)))
  }

  const handleMouseDown = (e: React.MouseEvent) => {
    if (zoom <= 1) return
    isDragging.current = true
    setIsDraggingStyle(true)
    dragStart.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging.current) return
    setPan({
      x: dragStart.current.px + e.clientX - dragStart.current.x,
      y: dragStart.current.py + e.clientY - dragStart.current.y,
    })
  }

  const handleMouseUp = () => { isDragging.current = false; setIsDraggingStyle(false) }

  return (
    <div
      className="fixed inset-0 z-50 bg-black flex items-center justify-center"
      style={{ cursor: zoom > 1 ? 'grab' : 'default' }}
      onDoubleClick={onClose}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
    >
      <img
        src={imgUrl}
        alt={alt}
        draggable={false}
        className="select-none"
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${orientDeg}deg)`,
          maxWidth: zoom <= 1 ? '100vw' : 'none',
          maxHeight: zoom <= 1 ? '100vh' : 'none',
          transition: isDraggingStyle ? 'none' : 'transform 0.1s ease',
        }}
      />

      {/* Close */}
      <button
        className="absolute top-4 right-4 bg-white/10 hover:bg-white/20 text-white rounded-full p-2"
        onClick={(e) => { e.stopPropagation(); onClose() }}
      >
        <svg className="w-5 h-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>

      {/* Zoom indicator */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-black/50 text-white text-sm px-3 py-1 rounded-full">
        {Math.round(zoom * 100)}%
      </div>

      {/* Nav */}
      {hasPrev && (
        <button
          className="absolute left-4 top-1/2 -translate-y-1/2 bg-white/10 hover:bg-white/20 text-white rounded-full w-10 h-10 flex items-center justify-center text-xl"
          onClick={(e) => { e.stopPropagation(); onNavigate(-1) }}
        >‹</button>
      )}
      {hasNext && (
        <button
          className="absolute right-4 top-1/2 -translate-y-1/2 bg-white/10 hover:bg-white/20 text-white rounded-full w-10 h-10 flex items-center justify-center text-xl"
          onClick={(e) => { e.stopPropagation(); onNavigate(1) }}
        >›</button>
      )}
    </div>
  )
}

function _orientDeg(orientation: number | null | undefined): number {
  switch (orientation) {
    case 3: return 180
    case 6: return 90
    case 8: return -90
    default: return 0
  }
}
