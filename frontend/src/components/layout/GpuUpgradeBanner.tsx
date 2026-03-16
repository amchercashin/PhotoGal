import { useState, useEffect } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { useDeviceInfo } from '../../hooks/useDeviceInfo'

type DownloadState = 'idle' | 'downloading' | 'success' | 'error'

export function GpuUpgradeBanner() {
  const { data: device } = useDeviceInfo()
  const [cudaInstalled, setCudaInstalled] = useState<boolean | null>(null)
  const [state, setState] = useState<DownloadState>('idle')
  const [downloadedMb, setDownloadedMb] = useState(0)
  const [error, setError] = useState('')

  useEffect(() => {
    invoke('check_cuda_status')
      .then((res: any) => setCudaInstalled(res.installed))
      .catch(() => setCudaInstalled(false))
  }, [])

  useEffect(() => {
    const unlisten = listen('cuda-download-progress', (event: any) => {
      setDownloadedMb(Math.round(event.payload.downloaded_mb))
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
    setError('')
    try {
      await invoke('download_cuda_addon')
      setState('success')
      setTimeout(() => setCudaInstalled(true), 2000)
    } catch (e: any) {
      setState('error')
      setError(String(e))
    }
  }

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
        <div className="flex items-center gap-3">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-amber-400 border-t-transparent" />
          <p className="text-sm text-amber-200">
            Скачивание GPU-ускорения... {downloadedMb} MB
          </p>
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
