import { useState } from 'react'
import { useDeviceInfo } from '../../hooks/useDeviceInfo'

const DISMISSED_KEY = 'gpu-upgrade-dismissed'

export function GpuUpgradeBanner() {
  const { data: device } = useDeviceInfo()
  const [dismissed, setDismissed] = useState(
    () => localStorage.getItem(DISMISSED_KEY) === 'true',
  )

  if (!device?.upgrade_available || dismissed) return null

  const sizeLabel = device.upgrade_size_mb
    ? `~${(device.upgrade_size_mb / 1024).toFixed(1)} GB`
    : 'minimal'

  const handleDismiss = () => {
    localStorage.setItem(DISMISSED_KEY, 'true')
    setDismissed(true)
  }

  return (
    <div className="mx-4 mt-2 rounded-lg border border-amber-700/50 bg-amber-950/50 px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-amber-200">
            {device.gpu_detected}
          </p>
          <p className="mt-1 text-xs text-amber-200/70">
            GPU-ускорение ({sizeLabel}) — {device.upgrade_benefit}
          </p>
        </div>
        <button
          onClick={handleDismiss}
          className="shrink-0 rounded px-3 py-1.5 text-xs text-amber-200/60 hover:text-amber-200"
        >
          Не сейчас
        </button>
      </div>
    </div>
  )
}
