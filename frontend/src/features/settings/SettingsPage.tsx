import { useNavigate } from 'react-router-dom'
import { FileType, Tags, ChevronRight } from 'lucide-react'

export function SettingsPage() {
  const navigate = useNavigate()

  const categories = [
    {
      id: 'entry-types',
      title: 'Entry Types',
      description: 'Manage content types, icons, and properties',
      icon: FileType,
      path: '/settings/entry-types',
      color: 'text-blue-500',
      bgColor: 'bg-blue-500/10'
    },
    {
      id: 'tags',
      title: 'Tags',
      description: 'Manage tags for organizing your content',
      icon: Tags,
      path: '/settings/tags',
      color: 'text-green-500',
      bgColor: 'bg-green-500/10'
    }
  ]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">Manage your application preferences</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {categories.map((category) => (
          <button
            key={category.id}
            onClick={() => navigate(category.path)}
            className="group flex items-start gap-4 p-4 rounded-xl border bg-card hover:bg-accent/50 transition-all text-left"
          >
            <div className={`p-3 rounded-lg ${category.bgColor} ${category.color}`}>
              <category.icon className="w-6 h-6" />
            </div>

            <div className="flex-1">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-lg">{category.title}</h3>
                <ChevronRight className="w-5 h-5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
              <p className="text-sm text-muted-foreground mt-1">
                {category.description}
              </p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
