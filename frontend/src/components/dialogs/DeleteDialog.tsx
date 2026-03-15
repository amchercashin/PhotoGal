/**
 * Two-step confirmation dialog for deleting photos from disk.
 * Step 1: Warning about disk deletion.
 * Step 2: Final confirmation.
 * Files are moved to system Trash.
 */

import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { toast } from '../../store/toast'

interface Props {
  photoIds: number[]
  onClose: () => void
  onDone: () => void
}

export function DeleteDialog({ photoIds, onClose, onDone }: Props) {
  const [step, setStep] = useState<1 | 2>(1)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    dialogRef.current?.focus()
  }, [])

  useEffect(() => {
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [])

  async function handleDelete() {
    setDeleting(true)
    setError(null)
    try {
      const result = await api.deletePhotosBulk(photoIds)
      if (result.errors && result.errors.length > 0) {
        setError(`Deleted ${result.trashed} files, but ${result.errors.length} failed: ${result.errors.slice(0, 3).join('; ')}`)
        timerRef.current = setTimeout(onDone, 3000)
      } else {
        onDone()
      }
    } catch (e: any) {
      const msg = e.message ?? 'Unknown error'
      setError(msg)
      toast.error(msg)
      setDeleting(false)
    }
  }

  const count = photoIds.length
  const plural = count !== 1

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="bg-neutral-900 border border-neutral-700 rounded-lg p-5 w-96 shadow-xl outline-none"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') onClose() }}
      >
        {step === 1 && (
          <>
            <h2 className="text-white font-semibold text-sm mb-3">Delete from Disk</h2>
            <p className="text-neutral-300 text-xs mb-2">
              {plural ? `${count} photos` : '1 photo'} will be permanently removed from disk
              and moved to Trash.
            </p>
            <p className="text-neutral-500 text-xs mb-4">
              Photo records will also be removed from the database.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                className="px-4 py-1.5 text-xs bg-neutral-700 hover:bg-neutral-600 text-neutral-200 rounded"
                onClick={onClose}
              >Keep</button>
              <button
                className="px-4 py-1.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded font-medium"
                onClick={() => setStep(2)}
              >Delete</button>
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h2 className="text-white font-semibold text-sm mb-3">Are you sure?</h2>
            <p className="text-red-400 text-xs mb-2">
              This will delete {plural ? `${count} files` : '1 file'} from your disk.
              Files will be moved to Trash.
            </p>
            <p className="text-neutral-500 text-xs mb-4">
              This action cannot be undone from within the app.
            </p>

            {error && (
              <div className="text-red-400 text-xs mb-3 bg-red-900/20 rounded p-2">{error}</div>
            )}

            <div className="flex gap-2 justify-end">
              <button
                className="px-4 py-1.5 text-xs bg-neutral-700 hover:bg-neutral-600 text-neutral-200 rounded"
                onClick={onClose}
                disabled={deleting}
              >Keep</button>
              <button
                className="px-4 py-1.5 text-xs bg-red-700 hover:bg-red-600 text-white rounded font-medium disabled:opacity-50"
                onClick={handleDelete}
                disabled={deleting}
              >
                {deleting ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
