import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type Person } from '../../api/client'
import { PersonView } from './PersonView'

export function PeopleGrid() {
  const [selectedPerson, setSelectedPerson] = useState<Person | null>(null)
  const { data: persons, isLoading } = useQuery({
    queryKey: ['persons'],
    queryFn: () => api.listPersons(),
  })

  if (selectedPerson) {
    return (
      <PersonView
        person={selectedPerson}
        onBack={() => setSelectedPerson(null)}
      />
    )
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-neutral-500">
        Loading people...
      </div>
    )
  }

  if (!persons?.length) {
    return (
      <div className="flex items-center justify-center h-full text-neutral-500">
        No faces detected yet. Run L3 analysis first.
      </div>
    )
  }

  const totalFaces = persons.reduce((s, p) => s + p.face_count, 0)

  return (
    <div className="h-full overflow-auto p-4">
      <div className="text-neutral-500 text-sm mb-4">
        {persons.length} people &middot; {totalFaces.toLocaleString()} faces
      </div>
      <div className="grid gap-4" style={{
        gridTemplateColumns: 'repeat(auto-fill, minmax(100px, 1fr))',
      }}>
        {persons.map((p) => (
          <PersonCard
            key={p.id}
            person={p}
            onClick={() => setSelectedPerson(p)}
          />
        ))}
      </div>
    </div>
  )
}

function PersonCard({ person, onClick }: { person: Person; onClick: () => void }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(person.name || '')
  useEffect(() => { setName(person.name || '') }, [person.name])
  const dimmed = person.face_count <= 3

  const renameMutation = useMutation({
    mutationFn: (newName: string) => api.updatePerson(person.id, { name: newName }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['persons'] }),
  })

  function handleRename() {
    if (name.trim() && name.trim() !== person.name) {
      renameMutation.mutate(name.trim())
    }
    setEditing(false)
  }

  const thumbUrl = person.representative_face_id
    ? api.getFaceThumbUrl(person.representative_face_id)
    : null

  return (
    <div
      className={`text-center cursor-pointer group ${dimmed ? 'opacity-40' : ''}`}
      onClick={() => !editing && onClick()}
    >
      <div className="w-20 h-20 mx-auto mb-2 rounded-full overflow-hidden bg-neutral-800 border-2 border-neutral-700 group-hover:border-teal-500 transition-colors">
        {thumbUrl ? (
          <img src={thumbUrl} alt="" className="w-full h-full object-cover" />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-2xl text-neutral-600">
            ?
          </div>
        )}
      </div>
      {editing ? (
        <input
          className="bg-neutral-800 text-white text-sm text-center w-full rounded px-1 py-0.5 outline-none focus:ring-1 focus:ring-teal-500"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={handleRename}
          onKeyDown={(e) => e.key === 'Enter' && handleRename()}
          autoFocus
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <div
          className="text-sm text-neutral-300 hover:text-white truncate cursor-text border-b border-dashed border-transparent hover:border-neutral-500"
          onClick={(e) => { e.stopPropagation(); setEditing(true) }}
        >
          {person.name || `Person ${person.id}`}
        </div>
      )}
      <div className="text-xs text-neutral-600">{person.face_count} photos</div>
    </div>
  )
}
