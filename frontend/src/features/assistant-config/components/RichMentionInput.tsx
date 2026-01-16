
import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Command, Braces } from 'lucide-react'
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
    // We use a specific class for styling.
    return text.replace(/{{([^}]+)}}/g, (match, key) => {
        return `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100/80 text-blue-600 select-none mx-0.5 align-baseline" contenteditable="false" data-variable="${key}">${key}</span>`
    })
}

// Utility to convert HTML back to raw text
const htmlToText = (html: string) => {
    const div = document.createElement('div')
    div.innerHTML = html

    // Replace chips with {{key}}
    const chips = div.querySelectorAll('[data-variable]')
    chips.forEach(chip => {
        const key = chip.getAttribute('data-variable')
        chip.replaceWith(`{{${key}}}`)
    })

    // Get text (preserving standard newlines if possible, though div usually does <br>)
    // Simple innerText might work, but we need to ensure <br> -> \n
    div.innerHTML = div.innerHTML.replace(/<br\s*\/?>/gi, '\n')
    return div.innerText
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
    const [slashIndex, setSlashIndex] = useState<number | null>(null) // Position in text where / was typed

    // Initialize content
    useEffect(() => {
        if (contentRef.current && value !== htmlToText(contentRef.current.innerHTML)) {
            // Only update if significantly different to avoid cursor jumps
            // Actually, for a controlled input with contentable, it's tricky.
            // We'll update only if the user isn't currently typing (checking focus maybe?)
            // Or simpler: We treat this as "Uncontrolled-ish". We sync OUT on input. 
            // We sync IN only if external value changes (e.g. initial load or reset).

            // For this use case (form tool editor), simple sync on mount + special care is often enough.
            // Let's do: Init once. If props change drastically, update.
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

        // Check for slash command
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

            // Look for / just before cursor
            // We support searching: /query
            // So we look back until whitespace or beginning
            const textBefore = text.slice(0, offset)
            const lastSlash = textBefore.lastIndexOf('/')

            if (lastSlash !== -1) {
                // Check if it's a valid trigger (start of string or preceded by space)
                // and no spaces after it (until cursor)
                const queryCandidate = textBefore.slice(lastSlash + 1)
                if (!queryCandidate.includes(' ')) {
                    // It's a match!
                    setFilter(queryCandidate)

                    // Calc position
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

        // We need to delete the text from slash to cursor
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

        // Insert the chip
        const chip = document.createElement('span')
        chip.className = "inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-600 select-none mx-0.5 align-baseline box-border border border-blue-200"
        chip.contentEditable = "false"
        chip.setAttribute('data-variable', paramName)
        chip.innerText = paramName // Or {{paramName}} if we want it to look like code, but user asked for chip

        range.insertNode(chip)

        // Move cursor after
        range.setStartAfter(chip)
        range.setEndAfter(chip)
        sel.removeAllRanges()
        sel.addRange(range)

        // Add a space after for convenience?
        const space = document.createTextNode('\u00A0') // Non-breaking space or normal? Normal is fine.
        range.insertNode(space)
        range.setStartAfter(space)
        range.setEndAfter(space)
        sel.removeAllRanges()
        sel.addRange(range)

        setShowMenu(false)
        handleInput() // Update state
    }

    // Handle click outside
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (showMenu && contentRef.current && !contentRef.current.contains(e.target as Node) && !(e.target as Element).closest('.mention-menu')) {
                setShowMenu(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [showMenu])

    const filteredParams = inputParams.filter(p => !filter || p.name.toLowerCase().includes(filter.toLowerCase()))

    return (
        <div className={`relative w-full group ${className}`}>
            <div
                ref={contentRef}
                contentEditable
                onInput={handleInput}
                onKeyDown={(e) => {
                    if (showMenu) {
                        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                            e.preventDefault()
                            // Todo: Keyboard nav
                        } else if (e.key === 'Enter') {
                            e.preventDefault()
                            if (filteredParams.length > 0) insertVariable(filteredParams[0].name)
                        } else if (e.key === 'Escape') {
                            setShowMenu(false)
                        }
                    }
                }}
                className={`w-full px-3 py-2 text-sm rounded-md border bg-background focus:outline-none focus:ring-1 focus:ring-primary/20 transition-all ${multiline ? 'min-h-[100px] whitespace-pre-wrap' : 'min-h-[38px] whitespace-nowrap overflow-x-auto scrolbar-hide'}`}
                style={{
                    height: multiline ? (rows * 20 + 20) + 'px' : 'auto'
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
                    className="mention-menu fixed z-[9999] w-64 max-h-60 overflow-y-auto rounded-lg border bg-popover shadow-xl animate-in fade-in zoom-in-95 flex flex-col p-1"
                    style={{ top: menuPos.top, left: menuPos.left }}
                >
                    <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground border-b mb-1 flex items-center gap-1.5">
                        <span className="bg-primary/10 text-primary px-1 rounded text-[10px] font-mono">{'{x}'}</span>
                        {t('settings.tools.inputParams')}
                    </div>

                    {filteredParams.length === 0 ? (
                        <div className="p-2 text-xs text-muted-foreground text-center">No params found</div>
                    ) : (
                        <div className="flex flex-col gap-0.5">
                            {filteredParams.map((param, idx) => (
                                <button
                                    key={param.name}
                                    onMouseDown={(e) => {
                                        e.preventDefault()
                                        e.stopPropagation()
                                        insertVariable(param.name)
                                    }}
                                    className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-md hover:bg-blue-50 hover:text-blue-700 text-left transition-colors group/item"
                                >
                                    <span className="w-5 h-5 flex items-center justify-center rounded-sm bg-blue-100 text-blue-600 text-[10px] font-bold shrink-0">
                                        V
                                    </span>
                                    <div className="flex flex-col flex-1 min-w-0">
                                        <span className="font-medium truncate leading-none">{param.name}</span>
                                        {param.description && <span className="text-[10px] text-muted-foreground truncate mt-0.5 group-hover/item:text-blue-600/70">{param.description}</span>}
                                    </div>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
