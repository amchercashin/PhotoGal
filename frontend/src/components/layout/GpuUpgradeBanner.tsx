import { useState, useEffect, useRef } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { useQueryClient } from '@tanstack/react-query'
import { useDeviceInfo } from '../../hooks/useDeviceInfo'

type BannerState = 'idle' | 'downloading' | 'success' | 'error' | 'retrying'

export function GpuUpgradeBanner() {
  const { data: device } = useDeviceInfo()
  const queryClient = useQueryClient()
  const [cudaInstalled, setCudaInstalled] = useState<boolean | null>(null)
  const [state, setState] = useState<BannerState>('idle')
  const [stage, setStage] = useState<string>('downloading')
  const [downloadedMb, setDownloadedMb] = useState(0)
  const [totalMb, setTotalMb] = useState<number | null>(null)
  const [speed, setSpeed] = useState<number | null>(null)
  const [error, setError] = useState('')
  const downloadStart = useRef(0)

  useEffect(() => {
    invoke('check_cuda_status')
      .then((res: any) => setCudaInstalled(res.installed))
      .catch(() => setCudaInstalled(false))
  }, [])

  useEffect(() => {
    const unlisten = listen('cuda-download-progress', (event: any) => {
      const { downloaded_mb, total_mb, stage: s } = event.payload
      if (s) setStage(s)
      if (downloaded_mb != null) setDownloadedMb(Math.round(downloaded_mb))
      if (total_mb != null) setTotalMb(Math.round(total_mb))

      if (downloadStart.current > 0 && downloaded_mb > 0) {
        const elapsed = (Date.now() - downloadStart.current) / 1000
        if (elapsed > 1) {
          setSpeed(Math.round(downloaded_mb / elapsed * 10) / 10)
        }
      }
    })
    return () => { unlisten.then(fn => fn()) }
  }, [])

  // Visibility logic:
  // Hide if still loading
  if (cudaInstalled === null) return null
  // Hide if CUDA installed and not failed
  if (cudaInstalled && !device?.cuda_failed) return null
  // Hide if no upgrade available, no blocked reason, and no cuda_failed
  if (!device?.upgrade_available && !device?.upgrade_blocked_reason && !device?.cuda_failed) return null

  const sizeLabel = device?.upgrade_size_mb
    ? `~${(device.upgrade_size_mb / 1024).toFixed(1)} GB`
    : ''

  const handleDownload = async () => {
    setState('downloading')
    setDownloadedMb(0)
    setTotalMb(null)
    setSpeed(null)
    setError('')
    downloadStart.current = Date.now()
    try {
      const result: any = await invoke('download_cuda_addon')
      // Check if result is a fallback (JSON with status: "fallback")
      let isFallback = false
      if (result && typeof result === 'object' && result.status === 'fallback') {
        isFallback = true
      } else if (typeof result === 'string') {
        try {
          const parsed = JSON.parse(result)
          if (parsed.status === 'fallback') isFallback = true
        } catch { /* not JSON */ }
      }

      if (isFallback) {
        // Refresh device info — banner will update to show cuda_failed state
        await queryClient.invalidateQueries({ queryKey: ['device-info'] })
        setState('idle')
      } else {
        setState('success')
        setTimeout(() => {
          setCudaInstalled(true)
          queryClient.invalidateQueries({ queryKey: ['device-info'] })
        }, 2000)
      }
    } catch (e: any) {
      setState('error')
      setError(String(e))
    }
  }

  const handleRetry = async () => {
    setState('retrying')
    try {
      await invoke('retry_cuda')
    } catch { /* ignore */ }
    await queryClient.invalidateQueries({ queryKey: ['device-info'] })
    setState('idle')
  }

  const handleRedownload = async () => {
    // Clear quarantine first, then re-download
    try {
      await invoke('retry_cuda')
    } catch { /* ignore */ }
    await handleDownload()
  }

  const pct = totalMb && totalMb > 0 ? Math.min(100, (downloadedMb / totalMb) * 100) : null
  const etaMin = speed && speed > 0 && totalMb
    ? Math.ceil((totalMb - downloadedMb) / speed / 60)
    : null

  // --- CUDA Failed state ---
  if (device?.cuda_failed && state === 'idle') {
    return (
      <div className="mx-4 mt-2 rounded-lg border border-red-700/50 bg-red-950/50 px-4 py-3">
        <p className="text-sm text-red-200">
          GPU-ускорение не запустилось: {device.cuda_failed_reason}
        </p>
        <div className="mt-2 flex items-center gap-3">
          {device.cuda_fix_url && (
            <a
              href={device.cuda_fix_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-red-300 underline hover:text-red-200"
            >
              {device.cuda_fix_action ?? 'Подробнее'} →
            </a>
          )}
          {(device.cuda_driver_update_helps || device.cuda_fix_url?.includes('aka.ms')) ? (
            <button
              onClick={handleRetry}
              className="shrink-0 rounded bg-red-800 px-3 py-1 text-xs font-medium text-red-100 hover:bg-red-700"
            >
              Повторить
            </button>
          ) : (
            <button
              onClick={handleRedownload}
              className="shrink-0 rounded bg-red-800 px-3 py-1 text-xs font-medium text-red-100 hover:bg-red-700"
            >
              Скачать заново
            </button>
          )}
        </div>
      </div>
    )
  }

  // --- Retrying state ---
  if (state === 'retrying') {
    return (
      <div className="mx-4 mt-2 rounded-lg border border-amber-700/50 bg-amber-950/50 px-4 py-3">
        <div className="flex items-center gap-2 text-sm text-amber-200">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
          <span>Проверяем GPU-ускорение...</span>
        </div>
      </div>
    )
  }

  // --- Blocked state ---
  if (device?.upgrade_blocked_reason && !device?.cuda_failed && state === 'idle') {
    return (
      <div className="mx-4 mt-2 rounded-lg border border-amber-700/50 bg-amber-950/50 px-4 py-3">
        <p className="text-sm text-amber-200/70">
          Обнаружена <span className="font-medium">{device.gpu_detected ?? 'NVIDIA GPU'}</span>.
          {' '}{device.upgrade_blocked_reason}
        </p>
        {device.upgrade_fix_url && (
          <a
            href={device.upgrade_fix_url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 inline-block text-xs text-amber-300 underline hover:text-amber-200"
          >
            {device.upgrade_fix_action ?? 'Подробнее'} →
          </a>
        )}
      </div>
    )
  }

  // --- Available / Downloading / Success / Error states ---
  return (
    <div className="mx-4 mt-2 rounded-lg border border-amber-700/50 bg-amber-950/50 px-4 py-3">
      {state === 'idle' && (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-amber-200">
            Обнаружена <span className="font-medium">{device?.gpu_detected ?? 'NVIDIA GPU'}</span>.
            {' '}Скачайте GPU-ускорение для быстрой обработки {sizeLabel && `(${sizeLabel})`}
          </p>
          <button
            onClick={handleDownload}
            className="shrink-0 rounded bg-amber-700 px-4 py-1.5 text-xs font-medium text-amber-100 hover:bg-amber-600"
          >
            Скачать
          </button>
        </div>
      )}
      {state === 'downloading' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm text-amber-200">
            <span>
              {stage === 'extracting' ? 'Распаковка...' :
               stage === 'installing' ? 'Установка GPU-ускорения...' :
               'Скачивание GPU-ускорения...'}
            </span>
            {stage === 'downloading' && (
              <span>
                {downloadedMb}{totalMb ? ` / ${totalMb}` : ''} MB
                {speed != null && ` · ${speed} MB/s`}
                {etaMin != null && ` · ~${etaMin} мин`}
              </span>
            )}
          </div>
          <div className="h-1.5 rounded-full bg-amber-900/50 overflow-hidden">
            {stage !== 'downloading' || pct == null ? (
              <div className="h-full rounded-full bg-amber-500 animate-indeterminate" />
            ) : (
              <div
                className="h-full rounded-full bg-amber-500 transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            )}
          </div>
        </div>
      )}
      {state === 'success' && (
        <p className="text-sm text-green-400">
          GPU-ускорение установлено! Перезапуск...
        </p>
      )}
      {state === 'error' && (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-red-400">
            Ошибка: {error}
          </p>
          <button
            onClick={handleDownload}
            className="shrink-0 rounded bg-amber-700 px-4 py-1.5 text-xs font-medium text-amber-100 hover:bg-amber-600"
          >
            Повторить
          </button>
        </div>
      )}
    </div>
  )
}
