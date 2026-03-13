import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

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

  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox="0 0 1 1"
      preserveAspectRatio="none"
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
          />
          {f.person_name && (
            <text
              x={f.bbox_x}
              y={f.bbox_y - 0.008}
              fill="#5eead4"
              fontSize="0.018"
              fontFamily="system-ui"
            >
              {f.person_name}
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}
