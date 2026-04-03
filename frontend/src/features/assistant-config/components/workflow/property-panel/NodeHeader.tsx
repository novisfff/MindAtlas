
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { NodeType } from '../../../api/workflow'
import { Brain, Bot, GitBranch, ScanSearch, BookOpen, Wrench, Play, RefreshCw, Infinity, SendHorizontal, FileCode2, Equal, UserCheck, Globe } from 'lucide-react'

interface NodeHeaderProps {
    nodeType: NodeType
    label: string
    onLabelChange: (label: string) => void
    readOnly?: boolean
}

const NODE_ICONS: Record<string, React.ElementType> = {
    llm: Brain,
    agent: Bot,
    if_else: GitBranch,
    parameter_extractor: ScanSearch,
    knowledge_retrieval: BookOpen,
    tool: Wrench,
    start: Play,
    iteration: RefreshCw,
    loop: Infinity,
    code_executor: FileCode2,
    http_request: Globe,
    variable_assign: Equal,
    human_in_loop: UserCheck,
    output: SendHorizontal,
}

const NODE_COLORS: Record<string, string> = {
    start: 'bg-gradient-to-r from-emerald-100/90 to-green-100/90 border-emerald-200 text-emerald-700',
    llm: 'bg-gradient-to-r from-violet-100/90 to-purple-100/90 border-violet-200 text-violet-700',
    agent: 'bg-gradient-to-r from-indigo-100/90 to-sky-100/90 border-indigo-200 text-indigo-700',
    tool: 'bg-gradient-to-r from-sky-100/90 to-blue-100/90 border-sky-200 text-sky-700',
    if_else: 'bg-gradient-to-r from-amber-100/90 to-yellow-100/90 border-amber-200 text-amber-700',
    parameter_extractor: 'bg-gradient-to-r from-fuchsia-100/90 to-pink-100/90 border-fuchsia-200 text-fuchsia-700',
    knowledge_retrieval: 'bg-gradient-to-r from-teal-100/90 to-emerald-100/90 border-teal-200 text-teal-700',
    iteration: 'bg-gradient-to-r from-cyan-100/90 to-sky-100/90 border-cyan-200 text-cyan-700',
    loop: 'bg-gradient-to-r from-indigo-100/90 to-blue-100/90 border-indigo-200 text-indigo-700',
    code_executor: 'bg-gradient-to-r from-orange-100/90 to-amber-100/90 border-orange-200 text-orange-700',
    http_request: 'bg-gradient-to-r from-blue-100/90 to-indigo-100/90 border-blue-200 text-blue-700',
    variable_assign: 'bg-gradient-to-r from-lime-100/90 to-emerald-100/90 border-lime-200 text-lime-700',
    human_in_loop: 'bg-gradient-to-r from-blue-100/90 to-cyan-100/90 border-blue-200 text-blue-700',
    output: 'bg-gradient-to-r from-rose-100/90 to-orange-100/90 border-rose-200 text-rose-700',
}

export function NodeHeader({ nodeType, label, onLabelChange, readOnly = false }: NodeHeaderProps) {
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
        if (readOnly) {
            setIsEditing(false)
            setDraftLabel(label)
            return
        }
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
                <Icon className="w-5 h-5 flex-shrink-0" />
            </div>

            <div className="flex-1 min-w-0">
                <div className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">
                    {t(`settings.skills.nodeTypes.${nodeType}`)}
                </div>

                {isEditing && !readOnly ? (
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
                        className={`text-sm font-bold text-foreground truncate rounded px-1 -ml-1 py-0.5 transition-colors ${
                            readOnly ? 'cursor-default' : 'cursor-text hover:bg-accent/50'
                        }`}
                        onClick={() => {
                            if (readOnly) return
                            setIsEditing(true)
                        }}
                        title={readOnly ? label : 'Click to edit label'}
                    >
                        {label}
                    </div>
                )}
            </div>
        </div>
    )
}
