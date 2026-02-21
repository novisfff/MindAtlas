import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X, RefreshCw, History, RotateCcw, Eraser, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'

export interface VersionPanelRecord {
  id: string
  sequenceNo: number
  versionName: string
  versionSource: 'save' | 'publish'
  createdAt: string
  updatedAt: string
}

interface TargetVersionPanelProps {
  open: boolean
  loading?: boolean
  loadError?: string | null
  isSystemTarget?: boolean
  draftVersionId?: string | null
  publishedVersionId?: string | null
  versions: VersionPanelRecord[]
  clearing?: boolean
  deletingVersionId?: string | null
  restoringVersionId?: string | null
  onClose: () => void
  onRefresh: () => void
  onClear: () => void
  onDelete: (versionId: string) => void
  onRestore: (versionId: string) => void
}

export function TargetVersionPanel({
  open,
  loading = false,
  loadError = null,
  isSystemTarget = false,
  draftVersionId = null,
  publishedVersionId = null,
  versions,
  clearing = false,
  deletingVersionId = null,
  restoringVersionId = null,
  onClose,
  onRefresh,
  onClear,
  onDelete,
  onRestore,
}: TargetVersionPanelProps) {
  const { t } = useTranslation()
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false)
  const systemDefaultVersionId = useMemo(() => {
    if (!isSystemTarget) return null
    const publishVersions = versions.filter((item) => item.versionSource === 'publish')
    if (publishVersions.length === 0) return null
    return publishVersions.reduce((best, current) => (
      current.sequenceNo < best.sequenceNo ? current : best
    )).id
  }, [isSystemTarget, versions])

  const orderedVersions = useMemo(() => {
    if (!systemDefaultVersionId) return versions
    const defaultVersion = versions.find((item) => item.id === systemDefaultVersionId)
    if (!defaultVersion) return versions
    return [
      defaultVersion,
      ...versions.filter((item) => item.id !== systemDefaultVersionId),
    ]
  }, [systemDefaultVersionId, versions])

  const handleClearConfirm = () => {
    setClearConfirmOpen(false)
    onClear()
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/20">
      <div className="h-full w-full max-w-md bg-white shadow-xl border-l flex flex-col">
        <div className="px-4 py-3 border-b flex items-center justify-between">
          <div className="flex items-center gap-2">
            <History className="w-4 h-4" />
            <h3 className="text-sm font-semibold">{t('settings.skills.workflowActions.versionHistory')}</h3>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setClearConfirmOpen(true)}
              className="inline-flex items-center gap-1 rounded border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
              title={t('settings.skills.versioning.clearNonPublished')}
              disabled={loading || clearing || versions.length === 0}
            >
              <Eraser className={`w-3 h-3 ${clearing ? 'animate-spin' : ''}`} />
              {t('settings.skills.versioning.clearNonPublished')}
            </button>
            <button
              type="button"
              onClick={onRefresh}
              className="p-1.5 rounded hover:bg-muted"
              title={t('common.refresh')}
              disabled={loading}
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded hover:bg-muted"
              title={t('common.close')}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="px-4 py-3 border-b bg-muted/30">
          <div className="text-xs text-muted-foreground">{t('settings.skills.versioning.currentDraft')}</div>
          <div className="text-sm font-medium mt-1">{draftVersionId ? draftVersionId.slice(0, 8) : '-'}</div>
        </div>

        <div className="flex-1 overflow-auto p-3 space-y-2">
          {loadError && (
            <div className="rounded-md border border-red-200 bg-red-50 text-red-700 text-xs p-2">
              {t('settings.skills.versioning.loadFailed')}: {loadError}
            </div>
          )}

          {!loading && versions.length === 0 && (
            <div className="rounded-md border border-dashed text-sm text-muted-foreground p-4 text-center">
              {t('settings.skills.versioning.empty')}
            </div>
          )}

          {orderedVersions.map((item) => {
            const isDraft = draftVersionId === item.id
            const isPublished = publishedVersionId === item.id
            const isSystemDefault = systemDefaultVersionId === item.id
            const restoring = restoringVersionId === item.id
            const deleting = deletingVersionId === item.id
            const deleteDisabled = isDraft || isPublished || isSystemDefault || deleting || restoring
            return (
              <div
                key={item.id}
                className={`
                  rounded-lg border p-3 space-y-2
                  ${isSystemDefault
                    ? 'bg-amber-50 border-amber-300 shadow-sm'
                    : 'bg-white'}
                `}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-medium truncate">
                      {isSystemDefault
                        ? t('settings.skills.versioning.systemBaselineVersionName')
                        : item.versionName}
                    </div>
                    {!isSystemDefault && (
                      <div className="text-xs text-muted-foreground">
                        #{item.sequenceNo} · {new Date(item.createdAt).toLocaleString()}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1">
                    {isSystemDefault && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-200 text-amber-900">
                        {t('settings.skills.versioning.systemDefault')}
                      </span>
                    )}
                    {isPublished && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
                        {t('settings.skills.versioning.latestPublished')}
                      </span>
                    )}
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 uppercase">
                      {item.versionSource}
                    </span>
                  </div>
                </div>

                {isSystemDefault && (
                  <div className="text-[11px] text-amber-800/90">
                    {t('settings.skills.versioning.systemDefaultDesc')}
                  </div>
                )}

                <div className="flex items-center justify-between">
                  <div className="text-xs text-muted-foreground">
                    {isDraft
                      ? t('settings.skills.versioning.currentDraft')
                      : (isSystemDefault ? '' : item.id.slice(0, 8))}
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => onDelete(item.id)}
                      disabled={deleteDisabled}
                      className="inline-flex items-center gap-1 rounded-md border border-red-200 text-red-700 px-2 py-1 text-xs hover:bg-red-50 disabled:opacity-50"
                    >
                      <Trash2 className={`w-3 h-3 ${deleting ? 'animate-spin' : ''}`} />
                      {t('settings.skills.versioning.delete')}
                    </button>
                    <button
                      type="button"
                      onClick={() => onRestore(item.id)}
                      disabled={isDraft || restoring || deleting}
                      className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
                    >
                      <RotateCcw className={`w-3 h-3 ${restoring ? 'animate-spin' : ''}`} />
                      {t('settings.skills.versioning.restore')}
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <ConfirmDialog
        isOpen={clearConfirmOpen}
        title={t('settings.skills.versioning.clearNonPublished')}
        description={t('settings.skills.versioning.clearConfirm')}
        confirmText={t('actions.confirm')}
        cancelText={t('actions.cancel')}
        variant="destructive"
        isLoading={clearing}
        onCancel={() => setClearConfirmOpen(false)}
        onConfirm={handleClearConfirm}
      />
    </div>
  )
}
