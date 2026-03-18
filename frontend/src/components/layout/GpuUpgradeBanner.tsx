import { useState, useEffect, useRef } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { useDeviceInfo } from '../../hooks/useDeviceInfo'

type DownloadState = 'idle' | 'downloading' | 'success' | 'error'

export function GpuUpgradeBanner() {
  const { data: device } = useDeviceInfo()
  const [cudaInstalled, setCudaInstalled] = useState<boolean | null>(null)
  const [state, setState] = useState<DownloadState>('idle')
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
      const { downloaded_mb, total_mb } = event.payload
      setDownloadedMb(Math.round(downloaded_mb))
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

  // Don't show if: CUDA already installed, no NVIDIA GPU, or still loading
  if (cudaInstalled === null || cudaInstalled) return null
  if (!device?.upgrade_available) return null

  const sizeLabel = device.upgrade_size_mb
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
      await invoke('download_cuda_addon')
      setState('success')
      setTimeout(() => setCudaInstalled(true), 2000)
    } catch (e: any) {
      setState('error')
      setError(String(e))
    }
  }

  const pct = totalMb && totalMb > 0 ? Math.min(100, (downloadedMb / totalMb) * 100) : null
  const etaMin = speed && speed > 0 && totalMb
    ? Math.ceil((totalMb - downloadedMb) / speed / 60)
    : null

  return (
    <div className="mx-4 mt-2 rounded-lg border border-amber-700/50 bg-amber-950/50 px-4 py-3">
      {state === 'idle' && (
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm text-amber-200">
            Обнаружена <span className="font-medium">{device.gpu_detected ?? 'NVIDIA GPU'}</span>.
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
            <span>Скачивание GPU-ускорения...</span>
            <span>
              {downloadedMb}{totalMb ? ` / ${totalMb}` : ''} MB
              {speed != null && ` · ${speed} MB/s`}
              {etaMin != null && ` · ~${etaMin} мин`}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-amber-900/50 overflow-hidden">
            {pct != null ? (
              <div
                className="h-full rounded-full bg-amber-500 transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            ) : (
              <div className="h-full rounded-full bg-amber-500 animate-indeterminate" />
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
