/**
 * A single cell in the Gallery grid.
 * Represents one cluster (or singleton photo).
 *
 * - Shows best photo thumbnail
 * - Badge top-right when cluster has >1 photo
 * - Visual states: selected (blue border), marked (dimmed + checkmark)
 */

import { memo } from 'react'
import type { Cluster } from '../../api/client'
import { PhotoThumbnail } from '../shared/PhotoThumbnail'

interface Props {
  cluster: Cluster
  isSelected: boolean
  isMarked: boolean
  similarity?: number
  onSelect: (e: React.MouseEvent) => void
  onDoubleClick: () => void
}

export const ClusterCell = memo(function ClusterCell({
  cluster,
  isSelected,
  isMarked,
  similarity,
  onSelect,
  onDoubleClick,
}: Props) {
  const photoId = cluster.best_photo_id

  return (
    <div
      className={[
        'relative w-full h-full overflow-hidden rounded cursor-pointer select-none',
        'transition-all duration-100',
        isSelected
          ? 'ring-2 ring-blue-400 ring-offset-1 ring-offset-neutral-950'
          : 'ring-1 ring-transparent hover:ring-neutral-600',
        isMarked ? 'opacity-50' : '',
      ].join(' ')}
      onClick={onSelect}
      onDoubleClick={onDoubleClick}
    >
      {/* Thumbnail */}
      {photoId ? (
        <PhotoThumbnail
          photoId={photoId}
          className="w-full h-full object-cover"
          style={{ display: 'block' }}
        />
      ) : (
        <div className="w-full h-full bg-neutral-800 flex items-center justify-center text-neutral-600 text-sm">
          no photo
        </div>
      )}

      {/* Similarity badge (search mode) */}
      {similarity != null && (
        <div className="absolute top-1.5 left-1.5 bg-green-700/80 backdrop-blur-sm text-white text-[10px] font-semibold px-1.5 py-0.5 rounded">
          {Math.round(similarity * 100)}%
        </div>
      )}

      {/* Cluster count badge */}
      {cluster.photo_count > 1 && (
        <div className="absolute top-1.5 right-1.5 bg-black/70 backdrop-blur-sm text-white text-xs font-semibold px-1.5 py-0.5 rounded flex items-center gap-1">
          <svg className="w-3 h-3 opacity-80" fill="currentColor" viewBox="0 0 16 16">
            <path d="M2 3a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V3zm3-1H3a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V3a2 2 0 0 0-2-2h-2V0h-2v1H5V0H5v1z"/>
          </svg>
          {cluster.photo_count}
        </div>
      )}

      {/* Marked checkmark overlay */}
      {isMarked && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="bg-blue-500/90 rounded-full p-1.5">
            <svg className="w-4 h-4 text-white" fill="none" stroke="currentColor" strokeWidth={3} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          </div>
        </div>
      )}

      {/* Quality / dup badges (bottom-left) */}
      <div className="absolute bottom-1 left-1 flex gap-0.5 flex-wrap">
        {(cluster.type === 'dup' || cluster.has_exact_duplicate) && (
          <span className="bg-orange-700/80 text-white text-[10px] px-1 py-0.5 rounded">dup</span>
        )}
        {cluster.best_photo_blur !== null && cluster.best_photo_blur !== undefined && cluster.best_photo_blur < 100 && (
          <span className="bg-red-700/80 text-white text-[10px] px-1 py-0.5 rounded">blur</span>
        )}
        {cluster.best_photo_exposure !== null && cluster.best_photo_exposure !== undefined && cluster.best_photo_exposure > 220 && (
          <span className="bg-yellow-600/80 text-white text-[10px] px-1 py-0.5 rounded">o-exp</span>
        )}
        {cluster.best_photo_exposure !== null && cluster.best_photo_exposure !== undefined && cluster.best_photo_exposure < 50 && (
          <span className="bg-blue-700/80 text-white text-[10px] px-1 py-0.5 rounded">u-exp</span>
        )}
      </div>

      {/* Date label */}
      {cluster.avg_timestamp && (
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent px-1.5 pt-3 pb-0.5 text-[10px] text-white/70 truncate text-right">
          {cluster.avg_timestamp.slice(0, 10)}
        </div>
      )}
    </div>
  )
})
