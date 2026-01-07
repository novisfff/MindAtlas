import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { TypeManager } from '../components/TypeManager'

export function EntryTypeSettings() {
  const navigate = useNavigate()

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate('/settings')}
          className="inline-flex items-center justify-center w-8 h-8 rounded-full hover:bg-muted transition-colors"
        >
          <ArrowLeft className="w-5 h-5 text-muted-foreground" />
        </button>
        <div>
          <h1 className="text-2xl font-bold">Entry Types</h1>
          <p className="text-muted-foreground">Manage your content types and their properties</p>
        </div>
      </div>

      <div className="rounded-xl border bg-card p-6">
        <TypeManager />
      </div>
    </div>
  )
}
