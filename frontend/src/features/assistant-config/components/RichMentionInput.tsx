
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { Command, Braces, ChevronRight, ChevronDown, Type, AlignLeft, Hash, Tag, Box } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { InputParam } from '../api/tools'

interface RichMentionInputProps {
    value: string
    onChange: (value: string) => void
    inputParams: InputParam[]
    placeholder?: string
    className?: string
    multiline?: boolean
    rows?: number // Approximation for height
}

// Utility to convert raw text (with {{param}}) to HTML with chips
const textToHtml = (text: string, params: InputParam[]) => {
    if (!text) return ''
    // Replace {{key}} with <span class="chip">{{key}}</span>
    return text.replace(/{{([^}]+)}}/g, (match, key) => {
        return `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100/80 text-blue-600 select-none mx-0.5 align-baseline" contenteditable="false" data-variable="${key}">${key}</span>`
    })
}

// Utility to convert HTML back to raw text
const htmlToText = (html: string) => {
    const div = document.createElement('div')
    div.innerHTML = html

    const chips = div.querySelectorAll('[data-variable]')
    chips.forEach(chip => {
        const key = chip.getAttribute('data-variable')
        chip.replaceWith(`{{${key}}}`)
    })

    div.innerHTML = div.innerHTML.replace(/<br\s*\/?>/gi, '\n')
    return div.innerText
}

interface ParamGroup {
    id: string
    label: string
    params: InputParam[]
}

export function RichMentionInput({
    value,
    onChange,
    inputParams,
    placeholder,
    className = '',
    multiline = false,
    rows = 1,
}: RichMentionInputProps) {
    const { t } = useTranslation()
    const contentRef = useRef<HTMLDivElement>(null)
    const [showMenu, setShowMenu] = useState(false)
    const [menuPos, setMenuPos] = useState({ top: 0, left: 0 })
    const [filter, setFilter] = useState('')

    // Track expanded state for groups. Default all expanded for better discovery? Or collapsed?
    // Let's default to expanded for now as there aren't too many steps usually.
    const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

    // Initialize content
    useEffect(() => {
        if (contentRef.current && value !== htmlToText(contentRef.current.innerHTML)) {
            const currentText = htmlToText(contentRef.current.innerHTML)
            if (currentText !== value) {
                contentRef.current.innerHTML = textToHtml(value, inputParams)
            }
        }
    }, [value, inputParams])

    const handleInput = useCallback(() => {
        if (!contentRef.current) return
        const text = htmlToText(contentRef.current.innerHTML)
        if (text !== value) {
            onChange(text)
        }
        checkCursorForSlash()
    }, [value, onChange])

    const checkCursorForSlash = () => {
        const sel = window.getSelection()
        if (!sel || !sel.rangeCount) return

        const range = sel.getRangeAt(0)
        const textNode = range.startContainer

        if (textNode.nodeType === Node.TEXT_NODE && textNode.textContent) {
            const text = textNode.textContent
            const offset = range.startOffset
            const textBefore = text.slice(0, offset)
            const lastSlash = textBefore.lastIndexOf('/')

            if (lastSlash !== -1) {
                const queryCandidate = textBefore.slice(lastSlash + 1)
                if (!queryCandidate.includes(' ')) {
                    setFilter(queryCandidate)
                    const rect = range.getBoundingClientRect()
                    setMenuPos({
                        top: rect.bottom + 5,
                        left: rect.left
                    })
                    setShowMenu(true)
                    return
                }
            }
        }
        setShowMenu(false)
    }

    const insertVariable = (paramName: string) => {
        const sel = window.getSelection()
        if (!sel || !sel.rangeCount) return

        const range = sel.getRangeAt(0)
        const textNode = range.startContainer

        if (textNode.nodeType === Node.TEXT_NODE && textNode.textContent) {
            const text = textNode.textContent
            const offset = range.startOffset
            const textBefore = text.slice(0, offset)
            const lastSlash = textBefore.lastIndexOf('/')

            if (lastSlash !== -1) {
                range.setStart(textNode, lastSlash)
                range.setEnd(textNode, offset)
                range.deleteContents()
            }
        }

        const chip = document.createElement('span')
        chip.className = "inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100/80 text-blue-600 select-none mx-0.5 align-baseline border border-blue-200"
        chip.contentEditable = "false"
        chip.setAttribute('data-variable', paramName)
        chip.innerText = paramName
        range.insertNode(chip)

        range.setStartAfter(chip)
        range.setEndAfter(chip)

        const space = document.createTextNode('\u00A0')
        range.insertNode(space)
        range.setStartAfter(space)
        range.setEndAfter(space)

        sel.removeAllRanges()
        sel.addRange(range)

        setShowMenu(false)
        handleInput()
    }

    // Handle clicking outside
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (showMenu && contentRef.current && !contentRef.current.contains(e.target as Node) && !(e.target as Element).closest('.mention-menu')) {
                setShowMenu(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [showMenu])

    // Group parameters
    const groupedParams = useMemo(() => {
        const groups: ParamGroup[] = []
        const systemParams: InputParam[] = []
        const stepGroups: Map<string, InputParam[]> = new Map()

        inputParams.forEach(p => {
            if (p.name === 'user_input' || p.name === 'history' || p.name.startsWith('last_step_')) {
                systemParams.push(p)
            } else if (p.name.startsWith('step_')) {
                // Extract step number: step_1_result -> 1
                const match = p.name.match(/^step_(\d+)_/)
                if (match) {
                    const stepNum = match[1]
                    const key = `Step ${stepNum}`
                    if (!stepGroups.has(key)) stepGroups.set(key, [])
                    stepGroups.get(key)!.push(p)
                } else {
                    systemParams.push(p)
                }
            } else {
                systemParams.push(p)
            }
        })

        if (systemParams.length > 0) {
            groups.push({ id: 'context', label: 'Context & Previous', params: systemParams })
        }

        // Sort steps numerically if possible (though Map insertion order usually preserves if inserted in order)
        // inputParams are usually ordered by step index in StepEditor, so we should be fine.
        Array.from(stepGroups.entries()).forEach(([label, params]) => {
            groups.push({ id: label, label, params })
        })

        return groups
    }, [inputParams])

    // Initial expand
    useEffect(() => {
        const allIds = groupedParams.map(g => g.id)
        setExpandedGroups(new Set(allIds))
    }, [groupedParams.length]) // Only when groups structure changes

    const toggleGroup = (id: string) => {
        const newSet = new Set(expandedGroups)
        if (newSet.has(id)) {
            newSet.delete(id)
        } else {
            newSet.add(id)
        }
        setExpandedGroups(newSet)
    }

    const getParamIcon = (p: InputParam) => {
        if (p.paramType === 'object' || p.name.endsWith('_raw')) return <Braces className="w-3 h-3" />
        if (p.name.endsWith('_result')) return <Box className="w-3 h-3" /> // Box for main result
        if (p.name === 'user_input') return <Type className="w-3 h-3" />
        return <Tag className="w-3 h-3" /> // Generic field
    }

    return (
        <div className={`relative w-full group ${className}`}>
            <div
                ref={contentRef}
                contentEditable
                onInput={handleInput}
                onKeyDown={(e) => {
                    if (showMenu) {
                        if (e.key === 'Escape') {
                            setShowMenu(false)
                        } else if (e.key === 'Enter') {
                            // Basic Enter support (insert first visible)
                            // Ideally needs full keyboard nav
                            e.preventDefault()
                            // Find first visible param in expanded groups
                            for (const group of groupedParams) {
                                if (expandedGroups.has(group.id)) {
                                    const visibleParams = group.params.filter(p => !filter || p.name.toLowerCase().includes(filter.toLowerCase()))
                                    if (visibleParams.length > 0) {
                                        insertVariable(visibleParams[0].name)
                                        break
                                    }
                                }
                            }
                        }
                    }
                }}
                className={`w-full px-3 py-2 text-sm rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all ${multiline ? 'min-h-[100px] whitespace-pre-wrap break-all' : 'min-h-[38px] whitespace-nowrap overflow-x-auto scrolbar-hide'}`}
                style={{
                    height: 'auto',
                    maxHeight: multiline ? '400px' : 'auto',
                    overflowY: multiline ? 'auto' : 'hidden'
                }}
                role="textbox"
                aria-multiline={multiline}
            />

            {!value && (
                <div className="absolute top-2.5 left-3 text-sm text-muted-foreground pointer-events-none select-none">
                    {placeholder}
                </div>
            )}

            {showMenu && (
                <div
                    className="mention-menu fixed z-[9999] w-72 max-h-[320px] overflow-y-auto rounded-xl border bg-popover shadow-xl animate-in fade-in zoom-in-95 flex flex-col p-1.5"
                    style={{ top: menuPos.top, left: menuPos.left }}
                >
                    <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground border-b mb-1 flex items-center gap-1.5">
                        <span className="bg-primary/10 text-primary px-1 rounded text-[10px] font-mono">{'{x}'}</span>
                        {t('settings.tools.inputParams')}
                    </div>

                    <div className="flex flex-col gap-1 overflow-y-auto">
                        {groupedParams.map(group => {
                            // Check visibility based on filter
                            const groupParams = group.params
                            const visibleParams = groupParams.filter(p => !filter || p.name.toLowerCase().includes(filter.toLowerCase()))

                            if (visibleParams.length === 0) return null

                            const isExpanded = expandedGroups.has(group.id)

                            return (
                                <div key={group.id} className="flex flex-col">
                                    <button
                                        type="button"
                                        onClick={() => toggleGroup(group.id)}
                                        className="flex items-center gap-1 px-2 py-1.5 text-xs font-semibold text-muted-foreground hover:bg-muted/50 rounded-md transition-colors select-none"
                                    >
                                        {isExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                                        <span>{group.label}</span>
                                    </button>

                                    {isExpanded && (
                                        <div className="pl-2 flex flex-col gap-0.5 mt-0.5">
                                            {visibleParams.map(param => (
                                                <button
                                                    key={param.name}
                                                    onMouseDown={(e) => {
                                                        e.preventDefault()
                                                        e.stopPropagation()
                                                        insertVariable(param.name)
                                                    }}
                                                    className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-md hover:bg-primary/10 hover:text-primary text-left transition-colors group/item"
                                                >
                                                    <span className={`w-5 h-5 flex items-center justify-center rounded-sm bg-muted text-muted-foreground group-hover/item:bg-primary/20 group-hover/item:text-primary shrink-0 transition-colors`}>
                                                        {getParamIcon(param)}
                                                    </span>
                                                    <div className="flex flex-col flex-1 min-w-0">
                                                        <span className="font-medium truncate leading-none text-xs">{param.name}</span>
                                                        {param.description && <span className="text-[10px] text-muted-foreground truncate mt-0.5">{param.description}</span>}
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )
                        })}
                        {groupedParams.every(g => g.params.filter(p => !filter || p.name.includes(filter)).length === 0) && (
                            <div className="p-2 text-xs text-muted-foreground text-center">No variables found</div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
