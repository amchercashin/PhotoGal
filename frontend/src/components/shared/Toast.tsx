import { useToastStore } from '../../store/toast'

const colors = {
  error: 'bg-red-600',
  success: 'bg-green-600',
  info: 'bg-blue-600',
}

export function ToastContainer() {
  const toasts = useToastStore((s) => s.toasts)
  const remove = useToastStore((s) => s.removeToast)
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`${colors[t.type]} text-white px-4 py-2 rounded shadow-lg text-sm flex items-center gap-2`}
        >
          <span className="flex-1">{t.message}</span>
          <button onClick={() => remove(t.id)} className="opacity-70 hover:opacity-100">x</button>
        </div>
      ))}
    </div>
  )
}
