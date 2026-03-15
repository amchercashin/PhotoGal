import { useState, useEffect, useRef, useCallback } from 'react'
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type Person } from '../../api/client'
import { toast } from '../../store/toast'
import { useUIStore } from '../../store/ui'
import { PersonView } from './PersonView'

const PAGE_SIZE = 100

export function PeopleGrid() {
  const [selectedPerson, setSelectedPerson] = useState<Person | null>(null)
  const [showHidden, setShowHidden] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  const {
    data,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['persons', showHidden],
    queryFn: ({ pageParam = 0 }) =>
      api.listPersons({ include_hidden: showHidden, limit: PAGE_SIZE, offset: pageParam }),
    getNextPageParam: (lastPage, allPages) => {
      if (lastPage.length < PAGE_SIZE) return undefined
      return allPages.reduce((sum, page) => sum + page.length, 0)
    },
    initialPageParam: 0,
  })

  const persons = data?.pages.flat() ?? []

  // Auto-select person navigated from FaceOverlay
  useEffect(() => {
    const { activePerson, setActivePerson } = useUIStore.getState()
    if (activePerson && persons.length > 0) {
      const match = persons.find((p) => p.id === activePerson)
      if (match) {
        setSelectedPerson(match)
        setActivePerson(null)
      }
    }
  }, [persons])

  // Auto-load next page when scrolling near bottom
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el || !hasNextPage || isFetchingNextPage) return
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 200) {
      fetchNextPage()
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.addEventListener('scroll', handleScroll, { passive: true })
    return () => el.removeEventListener('scroll', handleScroll)
  }, [handleScroll])

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

  if (!persons.length) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-neutral-500 gap-2">
        <div>No faces detected yet. Run L3 analysis first.</div>
        {!showHidden && (
          <label className="flex items-center gap-2 text-xs text-neutral-600 cursor-pointer select-none mt-2">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => setShowHidden(e.target.checked)}
              className="accent-teal-500"
            />
            Show hidden
          </label>
        )}
      </div>
    )
  }

  const totalFaces = persons.reduce((s, p) => s + p.face_count, 0)

  return (
    <div ref={scrollRef} className="h-full overflow-auto p-4">
      <div className="flex items-center justify-between mb-4">
        <div className="text-neutral-500 text-sm">
          {persons.length} people &middot; {totalFaces.toLocaleString()} faces
        </div>
        <label className="flex items-center gap-2 text-xs text-neutral-500 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showHidden}
            onChange={(e) => setShowHidden(e.target.checked)}
            className="accent-teal-500"
          />
          Show hidden
        </label>
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
      {isFetchingNextPage && (
        <div className="text-center text-neutral-600 text-xs py-4">Loading more...</div>
      )}
    </div>
  )
}

function PersonCard({ person, onClick }: { person: Person; onClick: () => void }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(person.name || '')
  const [hovered, setHovered] = useState(false)
  useEffect(() => { setName(person.name || '') }, [person.name])
  const dimmed = person.face_count <= 3

  const renameMutation = useMutation({
    mutationFn: (newName: string) => api.updatePerson(person.id, { name: newName }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['persons'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  const hideMutation = useMutation({
    mutationFn: (hidden: boolean) => api.updatePerson(person.id, { hidden }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['persons'] }),
    onError: (e: Error) => toast.error(e.message),
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
      className={`text-center cursor-pointer group relative ${dimmed ? 'opacity-40' : ''} ${person.hidden ? 'opacity-30' : ''}`}
      onClick={() => !editing && onClick()}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
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

      {/* Eye toggle on hover */}
      {hovered && (
        <button
          className="absolute top-0 right-0 bg-neutral-800/80 hover:bg-neutral-700 text-neutral-300 rounded-full w-6 h-6 flex items-center justify-center text-xs"
          onClick={(e) => { e.stopPropagation(); hideMutation.mutate(!person.hidden) }}
          title={person.hidden ? 'Show person' : 'Hide person'}
        >
          {person.hidden ? '\u{1F441}' : '\u{1F648}'}
        </button>
      )}

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
