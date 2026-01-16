
import React, { useState, useRef, useEffect } from 'react'
import { Command } from 'lucide-react'
import type { InputParam } from '../api/tools'

interface MentionInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement | HTMLTextAreaElement>, 'onChange'> {
    value: string
    onChange: (value: string) => void
    inputParams: InputParam[]
    placeholder?: string
    className?: string
    multiline?: boolean
    rows?: number
}

export function MentionInput({
    value,
    onChange,
    inputParams,
    multiline = false,
    className = '',
    ...props
}: MentionInputProps) {
    const [showMenu, setShowMenu] = useState(false)
    const [menuPos, setMenuPos] = useState({ top: 0, left: 0 })
    const [filter, setFilter] = useState('')
    const inputRef = useRef<HTMLTextAreaElement & HTMLInputElement>(null)

    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (showMenu && inputRef.current && !inputRef.current.contains(e.target as Node)) {
                setShowMenu(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [showMenu])

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === '/') {
            // Small delay to allow cursor to move
            setTimeout(() => {
                if (!inputRef.current) return
                const rect = inputRef.current.getBoundingClientRect()
                // Simple positioning: near the input, bottom left for now (enhancing for cursor pos is harder without libs)
                // Ideally we'd measure text width. For now, let's just show it below the input.
                setMenuPos({
                    top: rect.height + 4,
                    left: 0
                })
                setFilter('')
                setShowMenu(true)
            }, 0)
        } else if (showMenu) {
            if (e.key === 'Escape') {
                setShowMenu(false)
            }
            // Let typing continue, we filter based on text after '/'? 
            // Actually simplest is just show menu on '/' and let user pick.
            // If they keep typing, we could filter.
            // For MVP, just show menu on / and close on click/selection.
        }
    }

    const handleSelect = (paramName: string) => {
        const cursor = inputRef.current?.selectionStart || value.length
        const textBefore = value.substring(0, cursor)
        const textAfter = value.substring(cursor)

        // Check if the last char was / (it should be if we just typed it)
        // If we opened via /, we want to replace the / if possible, or just append.
        // Actually, if user typed /, it's in the value.
        const lastSlash = textBefore.lastIndexOf('/')

        let newValue = ''
        if (lastSlash !== -1 && lastSlash === textBefore.length - 1) {
            // Replace the slash
            newValue = textBefore.substring(0, lastSlash) + `{{${paramName}}}` + textAfter
        } else {
            // Just insert
            newValue = textBefore + `{{${paramName}}}` + textAfter
        }

        onChange(newValue)
        setShowMenu(false)

        // Restore focus
        setTimeout(() => inputRef.current?.focus(), 0)
    }

    // Filter params
    const filteredParams = inputParams.filter(p => !filter || p.name.includes(filter))

    const InputComponent = multiline ? 'textarea' : 'input'

    return (
        <div className="relative w-full">
            {/* @ts-ignore - Dynamic component typings are tricky */}
            <InputComponent
                ref={inputRef}
                value={value}
                onChange={(e: any) => onChange(e.target.value)}
                onKeyDown={handleKeyDown}
                className={className}
                autoComplete="off"
                {...props}
            />

            {showMenu && (
                <div
                    className="absolute z-50 w-64 max-h-48 overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md animate-in fade-in zoom-in-95"
                    style={{ top: menuPos.top, left: menuPos.left }}
                >
                    {filteredParams.length === 0 ? (
                        <div className="p-2 text-xs text-muted-foreground text-center">No params found</div>
                    ) : (
                        <div className="flex flex-col gap-0.5">
                            <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground border-b mb-1">
                                Insert Parameter
                            </div>
                            {filteredParams.map(param => (
                                <button
                                    key={param.name}
                                    type="button"
                                    onClick={() => handleSelect(param.name)}
                                    className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-sm hover:bg-accent hover:text-accent-foreground text-left"
                                >
                                    <span className="shrink-0 bg-primary/10 text-primary px-1 rounded text-[10px] font-mono">
                                        {param.paramType}
                                    </span>
                                    <span className="font-medium truncate">{param.name}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
