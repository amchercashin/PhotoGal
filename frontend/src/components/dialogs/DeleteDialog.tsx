/**
 * Confirmation dialog for bulk-deleting photo records from DB.
 * Files on disk are NOT touched.
 */

import { useState } from 'react'
import { api } from '../../api/client'

interface Props {
  photoIds: number[]
  onClose: () => void
  onDone: () => void
}

export function DeleteDialog({ photoIds, onClose, onDone }: Props) {
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleDelete() {
    setDeleting(true)
    setError(null)
    try {
      await api.deletePhotosBulk(photoIds)
      onDone()
    } catch (e: any) {
      setError(e.message ?? 'Unknown error')
      setDeleting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-5 w-80 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-white font-semibold text-sm mb-2">Delete Records</h2>
        <p className="text-neutral-400 text-xs mb-1">
          Remove <span className="text-white font-medium">{photoIds.length}</span> photo record{photoIds.length !== 1 ? 's' : ''} from the database.
        </p>
        <p className="text-neutral-500 text-xs mb-4">
          Files on disk will not be touched.
        </p>

        {error && (
          <div className="text-red-400 text-xs mb-3 bg-red-900/20 rounded p-2">{error}</div>
        )}

        <div className="flex gap-2 justify-end">
          <button
            className="px-3 py-1.5 text-xs bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded"
            onClick={onClose}
            disabled={deleting}
          >Cancel</button>
          <button
            className="px-3 py-1.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded disabled:opacity-50"
            onClick={handleDelete}
            disabled={deleting}
          >
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}
