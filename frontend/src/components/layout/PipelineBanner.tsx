/**
 * Non-blocking pipeline progress banner shown below Topbar during processing.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'

const LEVEL_NAMES: Record<number, string> = {
  0: 'Scanning',
  1: 'Analyzing',
  3: 'Face analysis',
}

const STAGE_LEVEL_NAMES: Record<string, string> = {
  'scanning':           'Scanning files',
  'quality':            'Analyzing quality',
  'clustering':         'Clustering',
  'geocoding':          'Geocoding',
  'downloading_model':  'Downloading AI model (~890 MB)...',
  'loading_model':      'Loading AI model...',
  'embeddings':         'AI embeddings',
  'merging':            'Merging clusters',
  'ranking':            'Ranking',
  'faces':              'Detecting faces',
  'face-clustering':    'Grouping faces',
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `~${Math.round(seconds)}s`
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60)
    const s = Math.round(seconds % 60)
    return s > 0 ? `~${m}m ${s}s` : `~${m}m`
  }
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return m > 0 ? `~${h}h ${m}m` : `~${h}h`
}

export function PipelineBanner() {
  const qc = useQueryClient()

  const { data: status } = useQuery({
    queryKey: ['pipeline-status'],
    queryFn: api.getPipelineStatus,
  })

  const stopMutation = useMutation({
    mutationFn: api.stopPipeline,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pipeline-status'] }),
  })

  if (!status?.running) return null

  const pct = status.total > 0
    ? Math.min(100, Math.round((status.progress / status.total) * 100))
    : null

  // Prefer stage-based label (shows current running level, not max_level)
  const levelLabel = (status.stage && STAGE_LEVEL_NAMES[status.stage])
    || (status.level != null ? (LEVEL_NAMES[status.level] ?? `Level ${status.level}`) : '')

  // ETA: compute from stage_elapsed_s + progress (per-stage rate)
  let etaText: string | null = null
  if (status.progress > 0 && status.total > 0 && status.stage_elapsed_s > 3) {
    const rate = status.progress / status.stage_elapsed_s
    const remaining = (status.total - status.progress) / rate
    if (remaining > 0) etaText = formatEta(remaining)
  }

  const isIndeterminate = status.stage === 'downloading_model' || status.stage === 'loading_model'

  return (
    <div className="bg-blue-950/80 border-b border-blue-800/50 px-4 py-1.5 flex items-center gap-3 text-xs shrink-0">
      <span className="text-blue-300 font-medium">{levelLabel}</span>

      {isIndeterminate && (
        <div className="flex items-center gap-2 flex-1">
          <div className="h-1.5 bg-blue-900 rounded overflow-hidden w-32">
            <div className="h-full bg-blue-500 animate-indeterminate" />
          </div>
          {status.stage === 'downloading_model' && (
            <span className="text-blue-500">First launch — downloading once</span>
          )}
        </div>
      )}

      {!isIndeterminate && pct != null && (
        <div className="flex items-center gap-2 flex-1">
          <div className="h-1.5 bg-blue-900 rounded overflow-hidden w-32">
            <div
              className="h-full bg-blue-500 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="text-blue-400">
            {status.progress.toLocaleString()}/{status.total.toLocaleString()}
          </span>
          {etaText && <span className="text-blue-500">{etaText}</span>}
        </div>
      )}

      <div className="flex-1" />

      <button
        className="text-red-400 hover:text-red-300 px-2 py-0.5 rounded hover:bg-red-900/30"
        onClick={() => stopMutation.mutate()}
      >Stop</button>
    </div>
  )
}
