import { useQuery } from '@tanstack/react-query'
import { api, type Person } from '../../api/client'

export function PersonView({ person, onBack }: { person: Person; onBack: () => void }) {
  const { data } = useQuery({
    queryKey: ['person-photos', person.id],
    queryFn: () => api.getPersonPhotos(person.id),
  })

  const thumbUrl = person.representative_face_id
    ? api.getFaceThumbUrl(person.representative_face_id)
    : null

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-neutral-800 shrink-0">
        <button
          onClick={onBack}
          className="text-teal-400 hover:text-teal-300 text-sm"
        >
          &larr; Back
        </button>
        <div className="w-9 h-9 rounded-full overflow-hidden bg-neutral-800">
          {thumbUrl ? (
            <img src={thumbUrl} alt="" className="w-full h-full object-cover" />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-neutral-600">?</div>
          )}
        </div>
        <div>
          <div className="text-white text-sm font-medium">
            {person.name || `Person ${person.id}`}
          </div>
          <div className="text-neutral-500 text-xs">
            {data?.total ?? '...'} photos
          </div>
        </div>
      </div>

      {/* Photo grid */}
      <div className="flex-1 overflow-auto p-2">
        {data?.photo_ids ? (
          <div className="grid gap-1" style={{
            gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
          }}>
            {data.photo_ids.map((pid) => (
              <div key={pid} className="aspect-square bg-neutral-900 rounded overflow-hidden">
                <img
                  src={api.thumbnailUrl(pid)}
                  alt=""
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="text-neutral-500 text-center mt-8">Loading...</div>
        )}
      </div>
    </div>
  )
}
