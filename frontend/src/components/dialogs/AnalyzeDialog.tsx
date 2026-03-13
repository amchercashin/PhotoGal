/**
 * Dialog for analyzing marked photos (full pipeline analysis).
 * Supports L3 face analysis via "Include face analysis" checkbox.
 */

import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'

interface Props {
  photoIds: number[]
  onClose: () => void
  onDone: () => void
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60)
    const s = Math.round(seconds % 60)
    return s > 0 ? `${m}m ${s}s` : `${m}m`
  }
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}

const LEVEL_DESCRIPTIONS: Record<number, Record<string, string>> = {
  // level: { withFaces, withoutFaces }
  0: { on: 'full analysis', off: 'full analysis' },
  1: { on: 'AI + faces', off: 'AI analysis' },
  2: { on: 'face analysis', off: 'done \u2713' },
  3: { on: 'done \u2713', off: 'done \u2713' },
}

export function AnalyzeDialog({ photoIds, onClose, onDone }: Props) {
  const qc = useQueryClient()
  const [includeFaces, setIncludeFaces] = useState(true)

  // Stable sorted copy: used for both cache key and query to ensure consistency
  const sortedPhotoIds = useMemo(
    () => [...photoIds].sort((a, b) => a - b),
    [photoIds],
  )
  const photoIdsKey = JSON.stringify(sortedPhotoIds)

  const { data: info, isLoading } = useQuery({
    queryKey: ['photo-level-info', photoIdsKey],
    queryFn: () => api.getPhotoLevelInfo(sortedPhotoIds),
    enabled: photoIds.length > 0,
  })

  const disconnectedCount = info?.disconnected_count ?? 0
  const levelCounts = info?.level_counts ?? { 0: 0, 1: 0, 2: 0, 3: 0 }

  const doneLevel = includeFaces ? 3 : 2
  const willProcess = Object.entries(levelCounts)
    .filter(([lvl]) => Number(lvl) < doneLevel)
    .reduce((sum, [, count]) => sum + count, 0)
  const allFullyAnalyzed = levelCounts[0] === 0 && levelCounts[1] === 0 && levelCounts[2] === 0 && levelCounts[3] > 0
  const alreadyAnalyzed = willProcess === 0

  const { data: estimate } = useQuery({
    queryKey: ['process-estimate', willProcess],
    queryFn: () => api.estimateProcessing(willProcess),
    enabled: willProcess > 0,
  })

  const estimatedSec = estimate?.estimated_seconds ?? 0
  const severity = estimatedSec < 60 ? 'none'
    : estimatedSec < 600 ? 'info'
    : estimatedSec < 3600 ? 'warn'
    : 'danger'
  const severityColor = severity === 'info' ? 'text-blue-400'
    : severity === 'warn' ? 'text-yellow-400'
    : severity === 'danger' ? 'text-red-400'
    : ''

  // Button text: "Detect Faces" if only L2→L3 work, otherwise "Analyze"
  const onlyFaceWork = willProcess > 0 && levelCounts[0] === 0 && levelCounts[1] === 0
  const buttonText = onlyFaceWork ? 'Detect Faces' : 'Analyze'

  async function handleConfirm() {
    try {
      await api.runMarked(photoIds, includeFaces ? 3 : 2)
      qc.invalidateQueries({ queryKey: ['pipeline-status'] })
      onDone()
    } catch (e: any) {
      console.error(e)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-5 w-80 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-white font-semibold text-sm mb-3">Analyze Photos</h2>

        {isLoading ? (
          <div className="text-neutral-500 text-xs mb-4">Loading…</div>
        ) : allFullyAnalyzed ? (
          <div className="text-neutral-400 text-xs mb-4">
            <div>{photoIds.length} marked</div>
            <div className="mt-2 text-neutral-500">All photos fully analyzed (L3).</div>
          </div>
        ) : (
          <div className="text-neutral-400 text-xs mb-4">
            {/* Summary line */}
            <div>
              {photoIds.length} marked
              {willProcess > 0 && <> · {willProcess} will be processed</>}
            </div>

            {/* Level breakdown */}
            {willProcess > 0 && (
              <div className="mt-2 space-y-0.5 text-neutral-500">
                {([0, 1, 2, 3] as const).map((lvl) => {
                  const count = levelCounts[lvl] ?? 0
                  if (count === 0) return null
                  const desc = LEVEL_DESCRIPTIONS[lvl]?.[includeFaces ? 'on' : 'off'] ?? ''
                  const isDone = desc.includes('\u2713')
                  return (
                    <div key={lvl} className={isDone ? 'text-neutral-600' : 'text-neutral-400'}>
                      {count} × {lvl === 0 ? 'Raw' : `L${lvl}`} — {desc}
                    </div>
                  )
                })}
              </div>
            )}

            {/* Disconnected */}
            {disconnectedCount > 0 && (
              <div className="mt-1 text-yellow-400">{disconnectedCount} disconnected (skipped)</div>
            )}

            {/* Already analyzed message (when checkbox toggled off) */}
            {alreadyAnalyzed && !allFullyAnalyzed && (
              <div className="mt-2 text-neutral-500">
                All photos already analyzed (L2).
              </div>
            )}

            {/* Estimate */}
            {estimate && estimatedSec >= 60 && willProcess > 0 && (
              <div className={`mt-2 ${severityColor}`}>
                {severity === 'danger' && '\u26A0 '}
                ~Estimated: {formatDuration(estimatedSec)}
                {estimate.source.startsWith('default') && ' (rough estimate)'}
                {severity === 'danger' && (
                  <div className="mt-1 text-neutral-500">
                    CLIP embeddings ~{estimate.rate_per_photo_ms}ms/photo.
                    Consider processing smaller batches.
                  </div>
                )}
              </div>
            )}

            {/* Include face analysis checkbox */}
            <label className="flex items-center gap-2 mt-3 cursor-pointer text-neutral-300 select-none">
              <input
                type="checkbox"
                checked={includeFaces}
                onChange={(e) => setIncludeFaces(e.target.checked)}
                className="accent-blue-500"
              />
              Include face analysis
            </label>
          </div>
        )}

        <div className="flex gap-2 justify-end">
          <button
            className="px-3 py-1.5 text-xs bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded"
            onClick={onClose}
          >{allFullyAnalyzed ? 'OK' : 'Cancel'}</button>
          {!allFullyAnalyzed && (
            <button
              className="px-3 py-1.5 text-xs bg-blue-700 hover:bg-blue-600 text-white rounded disabled:opacity-50"
              onClick={handleConfirm}
              disabled={isLoading || alreadyAnalyzed}
            >
              {buttonText}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
