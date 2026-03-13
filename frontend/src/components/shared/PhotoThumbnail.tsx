import { useState } from 'react'
import { api } from '../../api/client'

const MAX_RETRIES = 2

interface Props {
  photoId: number
  version?: string | null
  alt?: string
  className?: string
  style?: React.CSSProperties
}

export function PhotoThumbnail({ photoId, version, alt = '', className, style }: Props) {
  // Retry state resets naturally when parent uses key={photoId} or when
  // React remounts this component due to the key on <img> below.
  const [retry, setRetry] = useState(0)

  if (retry > MAX_RETRIES) {
    return (
      <div
        className={`flex items-center justify-center bg-neutral-800 text-neutral-600 text-xs ${className}`}
        style={style}
      >
        no image
      </div>
    )
  }

  // Key includes photoId to remount <img> when photo changes (resets retry state),
  // and retry to force a fresh <img> element on each retry attempt.
  // No loading="lazy" — virtualization already controls which images are in the DOM.
  return (
    <img
      key={`${photoId}-${retry}`}
      src={api.thumbnailUrl(photoId, version)}
      alt={alt}
      className={className}
      style={style}
      onError={() => setRetry((c) => c + 1)}
      draggable={false}
    />
  )
}
