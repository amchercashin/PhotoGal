/**
 * Central hook that detects pipeline running→stopped transitions
 * and invalidates relevant query caches so Gallery/Table update immediately.
 *
 * Also writes isPipelineRunning into UIStore so components don't need
 * their own pipeline-status queries.
 *
 * Mount once in App.tsx — works regardless of active tab.
 */

import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { useUIStore } from '../store/ui'

export function usePipelineSync() {
  const qc = useQueryClient()
  const wasRunningRef = useRef(false)
  const setIsPipelineRunning = useUIStore((s) => s.setIsPipelineRunning)

  const { data: status } = useQuery({
    queryKey: ['pipeline-status'],
    queryFn: api.getPipelineStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1000 : 5000),
  })

  const isRunning = status?.running ?? false

  useEffect(() => {
    setIsPipelineRunning(isRunning)

    // Detect running → stopped transition
    if (wasRunningRef.current && !isRunning) {
      qc.invalidateQueries({ queryKey: ['clusters'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['photos-table'] })
      qc.invalidateQueries({ queryKey: ['sources'] })
      qc.invalidateQueries({ queryKey: ['search'] })
    }
    wasRunningRef.current = isRunning
  }, [isRunning, setIsPipelineRunning, qc])

  return { isPipelineRunning: isRunning }
}
