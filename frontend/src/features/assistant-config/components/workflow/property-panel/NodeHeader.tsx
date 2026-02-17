
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { NodeType } from '../../../api/workflow'
import { Brain, GitBranch, ScanSearch, BookOpen, Wrench, Play, RefreshCw, Infinity, SendHorizontal } from 'lucide-react'

interface NodeHeaderProps {
    nodeType: NodeType
    label: string
    onLabelChange: (label: string) => void
}

const NODE_ICONS: Record<string, React.ElementType> = {
    llm: Brain,
    if_else: GitBranch,
    parameter_extractor: ScanSearch,
    knowledge_retrieval: BookOpen,
    tool: Wrench,
    start: Play,
    iteration: RefreshCw,
    loop: Infinity,
    output: SendHorizontal,
}

const NODE_COLORS: Record<string, string> = {
    llm: 'text-purple-600 bg-purple-50 border-purple-100',
    if_else: 'text-orange-600 bg-orange-50 border-orange-100',
    parameter_extractor: 'text-cyan-600 bg-cyan-50 border-cyan-100',
    knowledge_retrieval: 'text-green-600 bg-green-50 border-green-100',
    tool: 'text-sky-600 bg-sky-50 border-sky-100',
    start: 'text-emerald-600 bg-emerald-50 border-emerald-100',
    iteration: 'text-cyan-600 bg-cyan-50 border-cyan-100',
    loop: 'text-blue-600 bg-blue-50 border-blue-100',
    output: 'text-indigo-600 bg-indigo-50 border-indigo-100',
}

export function NodeHeader({ nodeType, label, onLabelChange }: NodeHeaderProps) {
    const { t } = useTranslation()
    const [isEditing, setIsEditing] = useState(false)
    const [draftLabel, setDraftLabel] = useState(label)

    // Sync draft label when prop changes
    useEffect(() => {
        setDraftLabel(label)
    }, [label])

    const Icon = NODE_ICONS[nodeType] || Brain
    const colorClass = NODE_COLORS[nodeType] || 'text-gray-600 bg-gray-50 border-gray-100'

    const handleBlur = () => {
        setIsEditing(false)
        if (draftLabel.trim() !== label) {
            onLabelChange(draftLabel)
        }
    }

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter') {
            handleBlur()
        }
    }

    return (
        <div className="flex items-center gap-3">
            <div className={`p-2.5 rounded-xl border ${colorClass} shadow-sm`}>
                <Icon className="w-5 h-5" />
            </div>

            <div className="flex-1 min-w-0">
                <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">
                    {t(`settings.skills.nodeTypes.${nodeType}`)}
                </div>

                {isEditing ? (
                    <input
                        autoFocus
                        type="text"
                        value={draftLabel}
                        onChange={(e) => setDraftLabel(e.target.value)}
                        onBlur={handleBlur}
                        onKeyDown={handleKeyDown}
                        className="w-full px-1.5 py-0.5 text-sm font-semibold bg-white border rounded shadow-sm focus:ring-2 focus:ring-primary/20 outline-none"
                    />
                ) : (
                    <div
                        className="text-sm font-bold text-foreground truncate cursor-text hover:bg-accent/50 rounded px-1 -ml-1 py-0.5 transition-colors"
                        onClick={() => setIsEditing(true)}
                        title="Click to edit label"
                    >
                        {label}
                    </div>
                )}
            </div>
        </div>
    )
}
