import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Box, Braces, ChevronDown, ChevronRight, Tag, Type } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { InputParam } from '../api/tools'

interface RichMentionInputProps {
  value: string
  onChange: (value: string) => void
  inputParams: InputParam[]
  placeholder?: string
  className?: string
  inputClassName?: string
  multiline?: boolean
  rows?: number
}

interface MentionItem {
  key: string
  insertValue: string
  itemLabel: string
  description?: string
  paramType: string
  searchText: string
}

interface MentionGroup {
  id: string
  label: string
  items: MentionItem[]
}

interface SlashContext {
  query: string
  range: Range
  selection: Selection
  rect: DOMRect
}

const CHIP_RE = /\{\{([^}]+)\}\}/g

const textToHtml = (text: string) => {
  if (!text) return ''
  return text.replace(CHIP_RE, (_, key: string) => {
    return `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100/80 text-blue-600 select-none mx-0.5 align-baseline border border-blue-200" contenteditable="false" data-variable="${key}">${key}</span>`
  })
}

const htmlToText = (html: string) => {
  const div = document.createElement('div')
  div.innerHTML = html
  const chips = div.querySelectorAll('[data-variable]')
  chips.forEach((chip) => {
    const key = chip.getAttribute('data-variable')
    chip.replaceWith(`{{${key}}}`)
  })
  div.innerHTML = div.innerHTML.replace(/<br\s*\/?>/gi, '\n')
  return div.innerText
}

function fallbackGroupFromName(name: string): { group: string; item: string } {
  const dot = name.indexOf('.')
  if (dot <= 0) return { group: 'Variables', item: name }
  const head = name.slice(0, dot)
  const tail = name.slice(dot + 1)
  if (head === 'sys') return { group: 'System Variables', item: tail }
  return { group: head, item: tail }
}

function buildMentionGroups(params: InputParam[]): MentionGroup[] {
  const grouped = new Map<string, MentionGroup>()

  params.forEach((param) => {
    const insertValue = param.referencePath || param.name
    const hasHierarchyMeta = Boolean(param.groupKey || param.groupLabel || param.itemLabel || param.referencePath)
    const fallback = fallbackGroupFromName(insertValue)
    const groupKey = hasHierarchyMeta ? (param.groupKey || `group:${param.groupLabel || fallback.group}`) : `group:${fallback.group}`
    const groupLabel = hasHierarchyMeta ? (param.groupLabel || fallback.group) : fallback.group
    const itemLabel = hasHierarchyMeta ? (param.itemLabel || fallback.item) : fallback.item

    const item: MentionItem = {
      key: `${groupKey}:${insertValue}:${itemLabel}`,
      insertValue,
      itemLabel,
      description: param.description,
      paramType: param.paramType,
      searchText: `${groupLabel} ${itemLabel} ${insertValue} ${param.description || ''}`.toLowerCase(),
    }

    const group = grouped.get(groupKey)
    if (group) {
      group.items.push(item)
      return
    }
    grouped.set(groupKey, { id: groupKey, label: groupLabel, items: [item] })
  })

  return Array.from(grouped.values())
}

function getSlashContext(contentEl: HTMLDivElement): SlashContext | null {
  const selection = window.getSelection()
  if (!selection || !selection.rangeCount) return null

  const range = selection.getRangeAt(0)
  if (!contentEl.contains(range.startContainer)) return null

  const prefixRange = range.cloneRange()
  prefixRange.selectNodeContents(contentEl)
  prefixRange.setEnd(range.startContainer, range.startOffset)
  const prefixText = prefixRange.toString().replace(/\u00A0/g, ' ')

  const slashIndex = prefixText.lastIndexOf('/')
  if (slashIndex === -1) return null

  const query = prefixText.slice(slashIndex + 1)
  if (/\s/.test(query)) return null

  return {
    query,
    range,
    selection,
    rect: range.getBoundingClientRect(),
  }
}

function deleteSlashQueryBeforeCaret(context: SlashContext): void {
  const removeLen = context.query.length + 1
  if (removeLen <= 0) return

  const { range } = context
  const container = range.startContainer
  const offset = range.startOffset

  if (container.nodeType === Node.TEXT_NODE) {
    if (offset < removeLen) return
    range.setStart(container, offset - removeLen)
    range.setEnd(container, offset)
    range.deleteContents()
    return
  }

  if (container.nodeType !== Node.ELEMENT_NODE) return
  const element = container as Element
  const prev = offset > 0 ? element.childNodes[offset - 1] : null
  if (!prev || prev.nodeType !== Node.TEXT_NODE) return
  const textNode = prev as Text
  const textLength = textNode.textContent?.length ?? 0
  if (textLength < removeLen) return
  range.setStart(textNode, textLength - removeLen)
  range.setEnd(textNode, textLength)
  range.deleteContents()
}

export function RichMentionInput({
  value,
  onChange,
  inputParams,
  placeholder,
  className = '',
  inputClassName,
  multiline = false,
}: RichMentionInputProps) {
  const { t } = useTranslation()
  const contentRef = useRef<HTMLDivElement>(null)
  const [showMenu, setShowMenu] = useState(false)
  const [menuPos, setMenuPos] = useState({ top: 0, left: 0 })
  const [filter, setFilter] = useState('')
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set())

  const menuPosition = useMemo(() => {
    const menuWidth = 288
    const menuHeight = 320
    const viewportWidth = typeof window !== 'undefined' ? window.innerWidth : 1280
    const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 800
    const left = Math.max(8, Math.min(menuPos.left, viewportWidth - menuWidth - 8))
    const top = Math.max(8, Math.min(menuPos.top, viewportHeight - menuHeight - 8))
    return { left, top }
  }, [menuPos.left, menuPos.top])

  useEffect(() => {
    if (!contentRef.current) return
    const currentText = htmlToText(contentRef.current.innerHTML)
    if (currentText !== value) {
      contentRef.current.innerHTML = textToHtml(value)
    }
  }, [value])

  const groupedParams = useMemo(() => buildMentionGroups(inputParams), [inputParams])

  useEffect(() => {
    setExpandedGroups(new Set(groupedParams.map((group) => group.id)))
  }, [groupedParams])

  const handleInput = useCallback(() => {
    if (!contentRef.current) return
    const text = htmlToText(contentRef.current.innerHTML)
    if (text !== value) {
      onChange(text)
    }

    const slashContext = getSlashContext(contentRef.current)
    if (!slashContext) {
      setShowMenu(false)
      return
    }

    setFilter(slashContext.query)
    setMenuPos({ top: slashContext.rect.bottom + 5, left: slashContext.rect.left })
    setShowMenu(true)
  }, [onChange, value])

  const insertVariable = useCallback(
    (paramName: string) => {
      const contentEl = contentRef.current
      if (!contentEl) return
      const slashContext = getSlashContext(contentEl)
      if (!slashContext) return
      const { selection, range } = slashContext

      deleteSlashQueryBeforeCaret(slashContext)

      const chip = document.createElement('span')
      chip.className = 'inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-blue-100/80 text-blue-600 select-none mx-0.5 align-baseline border border-blue-200'
      chip.contentEditable = 'false'
      chip.setAttribute('data-variable', paramName)
      chip.innerText = paramName
      range.insertNode(chip)

      range.setStartAfter(chip)
      range.setEndAfter(chip)
      const space = document.createTextNode('\u00A0')
      range.insertNode(space)
      range.setStartAfter(space)
      range.setEndAfter(space)

      selection.removeAllRanges()
      selection.addRange(range)
      setShowMenu(false)
      handleInput()
    },
    [handleInput],
  )

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Element | null
      if (!showMenu) return
      if (contentRef.current?.contains(target)) return
      if (target?.closest('.mention-menu')) return
      setShowMenu(false)
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [showMenu])

  const filteredGroups = useMemo(() => {
    const normalizedFilter = filter.trim().toLowerCase()
    return groupedParams
      .map((group) => ({
        ...group,
        items: normalizedFilter
          ? group.items.filter((item) => item.searchText.includes(normalizedFilter))
          : group.items,
      }))
      .filter((group) => group.items.length > 0)
  }, [filter, groupedParams])

  const toggleGroup = (id: string) => {
    const next = new Set(expandedGroups)
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
    }
    setExpandedGroups(next)
  }

  const getParamIcon = (item: MentionItem) => {
    if (item.paramType === 'object') return <Braces className="w-3 h-3" />
    if (item.paramType === 'array') return <Box className="w-3 h-3" />
    if (item.insertValue === 'start.user_input') return <Type className="w-3 h-3" />
    return <Tag className="w-3 h-3" />
  }

  return (
    <div className={`relative w-full group ${className}`}>
      <div
        ref={contentRef}
        contentEditable
        onInput={handleInput}
        onKeyUp={handleInput}
        onMouseUp={handleInput}
        onKeyDown={(e) => {
          if (!showMenu) return
          if (e.key === 'Escape') {
            setShowMenu(false)
            return
          }
          if (e.key !== 'Enter') return
          e.preventDefault()
          const first = filteredGroups.find((group) => group.items.length > 0)?.items[0]
          if (first) {
            insertVariable(first.insertValue)
          }
        }}
        className={inputClassName || `w-full px-3 py-2 text-sm rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20 transition-all ${multiline ? 'min-h-[56px] whitespace-pre-wrap break-all' : 'min-h-[38px] whitespace-nowrap overflow-x-auto scrolbar-hide'}`}
        style={{
          height: 'auto',
          maxHeight: multiline ? '400px' : 'auto',
          overflowY: multiline ? 'auto' : 'hidden',
        }}
        role="textbox"
        aria-multiline={multiline}
      />

      {(!value || value.trim() === '') && placeholder && (
        <div className="absolute top-2.5 left-3 text-sm text-slate-400 pointer-events-none select-none">
          {placeholder}
        </div>
      )}

      {showMenu && typeof document !== 'undefined' && createPortal(
        <div
          className="mention-menu fixed z-[99999] w-72 max-h-[320px] overflow-y-auto rounded-xl border bg-popover shadow-xl animate-in fade-in zoom-in-95 flex flex-col p-1.5"
          style={{ top: menuPosition.top, left: menuPosition.left }}
        >
          <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground border-b mb-1 flex items-center gap-1.5">
            <span className="bg-primary/10 text-primary px-1 rounded text-[10px] font-mono">{'{x}'}</span>
            {t('settings.tools.inputParams')}
          </div>

          <div className="flex flex-col gap-1 overflow-y-auto">
            {filteredGroups.map((group) => {
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
                      {group.items.map((item) => (
                        <button
                          key={item.key}
                          onMouseDown={(e) => {
                            e.preventDefault()
                            e.stopPropagation()
                            insertVariable(item.insertValue)
                          }}
                          className="flex items-center gap-2 w-full px-2 py-1.5 text-sm rounded-md hover:bg-primary/10 hover:text-primary text-left transition-colors group/item"
                        >
                          <span className="w-5 h-5 flex items-center justify-center rounded-sm bg-muted text-muted-foreground group-hover/item:bg-primary/20 group-hover/item:text-primary shrink-0 transition-colors">
                            {getParamIcon(item)}
                          </span>
                          <div className="flex flex-col flex-1 min-w-0">
                            <span className="font-medium truncate leading-none text-xs">{item.itemLabel}</span>
                            <span className="text-[10px] text-muted-foreground truncate mt-0.5">{item.insertValue}</span>
                            {item.description && <span className="text-[10px] text-muted-foreground truncate mt-0.5">{item.description}</span>}
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
            {filteredGroups.length === 0 && (
              <div className="p-2 text-xs text-muted-foreground text-center">No variables found</div>
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  )
}
