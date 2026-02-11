import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { format } from 'date-fns'
import { zhCN, enUS } from 'date-fns/locale'
import { Clock, X } from 'lucide-react'
import type { GraphColors } from '../hooks/useGraphData'

export interface TooltipState {
  x: number
  y: number
  data: any // Node or Link with D3 properties
  type: 'node' | 'link'
}

interface GraphTooltipProps {
  tooltip: TooltipState | null
  colors: GraphColors
  onClose: () => void
  selectedNode: any
  selectedLink: any
}

export function GraphTooltip({
  tooltip,
  colors,
  onClose,
  selectedNode,
  selectedLink
}: GraphTooltipProps) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()

  if (!tooltip) return null

  const dateLocale = i18n.language.startsWith('zh') ? zhCN : enUS
  const dateFormatStr = i18n.language.startsWith('zh') ? 'yyyy年M月d日' : 'MMM d, yyyy'
  const { x, y, data, type } = tooltip

  const renderNodeTooltip = (node: any) => {
    // Format time display
    let timeText = ''
    if (node.timeMode === 'POINT' && node.timeAt) {
      timeText = format(new Date(node.timeAt), dateFormatStr, { locale: dateLocale })
    } else if (node.timeMode === 'RANGE') {
      const from = node.timeFrom ? format(new Date(node.timeFrom), dateFormatStr, { locale: dateLocale }) : t('labels.unknown')
      const to = node.timeTo ? format(new Date(node.timeTo), dateFormatStr, { locale: dateLocale }) : t('time.present')
      timeText = `${from} → ${to}`
    }

    return (
      <div className="bg-white/95 dark:bg-zinc-900/95 backdrop-blur rounded-lg shadow-xl border border-zinc-200 dark:border-zinc-800 max-w-[280px] overflow-hidden relative group">
        <button
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          className="absolute top-2 right-2 p-1 rounded-full bg-white/50 dark:bg-black/20 hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors opacity-0 group-hover:opacity-100"
          title={t('actions.close')}
        >
          <X className="w-3 h-3" />
        </button>
        <div className="h-1 w-full" style={{ backgroundColor: node.color || colors.muted }}></div>
        <div className="p-3 pt-2">
          {/* Header: Label & Type */}
          <div className="flex items-center justify-between gap-2 mb-1 pr-6">
            <div className="font-semibold text-zinc-900 dark:text-zinc-100 text-sm truncate">{node.label}</div>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 border border-zinc-200 dark:border-zinc-700 whitespace-nowrap">
              {node.typeName}
            </span>
          </div>
          {/* LightRAG Metadata: Entity Type */}
          {node.entityType && (
            <div className="text-[10px] text-zinc-400 font-mono mb-2">
              {t('labels.type')}: {node.entityType}
            </div>
          )}
          {/* Summary/Description */}
          {node.summary ? (
            <div className="text-xs text-zinc-600 dark:text-zinc-400 line-clamp-3 leading-relaxed">
              {node.summary}
            </div>
          ) : (
            <div className="text-xs italic text-zinc-400">{t('pages.graph.noSummary')}</div>
          )}
          {/* Time info (for system graph) */}
          {timeText && (
            <div className="flex items-center text-[10px] text-zinc-500 mt-2 gap-1">
              <Clock className="w-3 h-3" />
              <span>{timeText}</span>
            </div>
          )}
          {/* Footer: Created At & Entry Link hint */}
          <div className={`text-[10px] text-zinc-400 ${timeText ? 'mt-1' : 'mt-2 pt-2 border-t border-zinc-100 dark:border-zinc-800'}`}>
            {node.createdAt && (
              <div>{t('labels.created')}: {format(new Date(node.createdAt), 'yyyy-MM-dd')}</div>
            )}
            {(node.entryTitle || node.entryId || node.attachmentTitle || node.attachmentId || !node.entityType) && (
              <div
                className="text-blue-500 mt-1 truncate max-w-[240px] cursor-pointer hover:underline"
                title={node.attachmentTitle || node.attachmentId || node.entryTitle || node.entryId || node.label}
                onClick={(e) => {
                  e.stopPropagation()
                  const targetId = node.entryId || node.id
                  if (targetId) {
                    if (node.attachmentId) {
                      navigate(`/entries/${targetId}#attachments`)
                    } else {
                      navigate(`/entries/${targetId}`)
                    }
                  }
                }}
              >
                {node.attachmentId
                  ? `${t('labels.sourceEntry')}: ${node.attachmentTitle || node.attachmentId}`
                  : `${t('labels.sourceEntry')}: ${node.entryTitle || node.label || (node.entryId ? `${node.entryId.slice(0, 8)}...` : node.id.slice(0, 8) + '...')}`}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  const renderLinkTooltip = (link: any) => {
    const source = typeof link.source === 'object' ? link.source : null
    const target = typeof link.target === 'object' ? link.target : null
    const sourceLabel = source?.label || String(link.source || '')
    const targetLabel = target?.label || String(link.target || '')

    return (
      <div className="p-3 bg-white/95 dark:bg-zinc-900/95 backdrop-blur rounded-lg shadow-xl border border-zinc-200 dark:border-zinc-800 max-w-[260px] relative group">
        <button
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          className="absolute top-2 right-2 p-1 rounded-full bg-zinc-50 dark:bg-zinc-900 hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors opacity-0 group-hover:opacity-100"
          title={t('actions.close')}
        >
          <X className="w-3 h-3" />
        </button>
        {/* Header: Source -> Target */}
        <div className={`${(sourceLabel.length + targetLabel.length > 20) ? 'text-[10px]' : 'text-xs font-medium'} text-zinc-900 dark:text-zinc-100 mb-2 flex items-center gap-2 ${(link.description || link.keywords) ? 'border-b border-zinc-100 dark:border-zinc-800 pb-2' : ''} pr-6`}>
          <span className="truncate max-w-[100px]" title={sourceLabel}>{sourceLabel}</span>
          <span className="text-zinc-400">→</span>
          <span className="truncate max-w-[100px]" title={targetLabel}>{targetLabel}</span>
        </div>

        {/* Description */}
        {link.description && (
          <div className="text-xs text-zinc-600 dark:text-zinc-400 italic mb-2 leading-relaxed line-clamp-3">
            "{link.description}"
          </div>
        )}

        {/* Keywords */}
        {link.keywords && (
          <div className="flex flex-wrap gap-1 mb-2">
            {link.keywords.split(',').map((kw: string, i: number) => (
              <span key={i} className="text-[10px] px-1 rounded bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border border-blue-100 dark:border-blue-800">
                {kw.trim()}
              </span>
            ))}
          </div>
        )}

        {/* Footer: Relation Type & Source Entry */}
        <div className={`flex flex-col gap-1 pt-2 border-t border-zinc-100 dark:border-zinc-800 ${(link.description || link.keywords) ? '' : 'mt-0'}`}>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500"></span>
              <span className="text-[10px] font-medium text-zinc-700 dark:text-zinc-300">{link.label}</span>
            </div>
            {link.createdAt && (
              <span className="text-[10px] text-zinc-400">{format(new Date(link.createdAt), 'yyyy-MM-dd')}</span>
            )}
          </div>

          {(link.entryTitle || link.entryId || link.attachmentTitle || link.attachmentId) && (
            <div className="flex justify-end items-center text-[10px] text-zinc-400 mt-1">
              <div
                className="text-blue-500 cursor-pointer hover:underline truncate max-w-[120px]"
                title={link.attachmentTitle || link.attachmentId || link.entryTitle || link.entryId}
                onClick={(e) => {
                  e.stopPropagation()
                  const targetId = link.entryId
                  if (targetId) {
                    if (link.attachmentId) {
                      navigate(`/entries/${targetId}#attachments`)
                    } else {
                      navigate(`/entries/${targetId}`)
                    }
                  }
                }}
              >
                {link.attachmentId
                  ? (link.attachmentTitle || link.attachmentId)
                  : (link.entryTitle || (link.entryId ? `${link.entryId.slice(0, 8)}...` : ''))}
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div
      className={`absolute z-20 transform -translate-x-1/2 -translate-y-full mb-2 animate-in fade-in zoom-in-95 duration-150 ${selectedNode || selectedLink ? 'pointer-events-auto' : 'pointer-events-none'}`}
      style={{ left: x, top: y }}
    >
      {type === 'node' ? renderNodeTooltip(data) : renderLinkTooltip(data)}
    </div>
  )
}
