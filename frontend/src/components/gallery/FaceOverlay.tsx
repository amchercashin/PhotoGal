import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useUIStore } from '../../store/ui'

interface Props {
  photoId: number
  visible: boolean
}

export function FaceOverlay({ photoId, visible }: Props) {
  const { data: faces } = useQuery({
    queryKey: ['photo-faces', photoId],
    queryFn: () => api.getFacesForPhoto(photoId),
    enabled: visible && photoId != null,
    staleTime: 60_000,
  })

  if (!visible || !faces?.length) return null

  const handleFaceClick = (personId: number | null) => {
    if (!personId) return
    const store = useUIStore.getState()
    store.setActivePerson(personId)
    store.setTab('people')
  }

  return (
    <svg
      className="absolute inset-0 w-full h-full"
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
      style={{ pointerEvents: 'none' }}
    >
      {faces.map((f) => (
        <g key={f.id}>
          <rect
            x={f.bbox_x}
            y={f.bbox_y}
            width={f.bbox_w}
            height={f.bbox_h}
            fill="none"
            stroke="#5eead4"
            strokeWidth="0.003"
            opacity={0.7}
            rx="0.005"
            style={{
              pointerEvents: f.person_id ? 'auto' : 'none',
              cursor: f.person_id ? 'pointer' : 'default',
            }}
            onClick={(e) => {
              e.stopPropagation()
              handleFaceClick(f.person_id)
            }}
          />
          {f.person_name && (
            <text
              x={f.bbox_x}
              y={f.bbox_y - 0.008}
              fill="#5eead4"
              fontSize="0.018"
              fontFamily="system-ui"
              textLength={f.person_name.length > 12 ? String(f.bbox_w) : undefined}
              lengthAdjust="spacing"
              style={{ pointerEvents: 'none' }}
            >
              {f.person_name.length > 15 ? f.person_name.slice(0, 14) + '\u2026' : f.person_name}
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}
