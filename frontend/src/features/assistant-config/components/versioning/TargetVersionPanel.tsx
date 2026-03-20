import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw, History, RotateCcw, Eraser, Trash2 } from 'lucide-react'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { WorkflowEditorSurfaceShell } from '../workflow/WorkflowEditorSurfaceShell'

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
    <>
      <WorkflowEditorSurfaceShell
        size="default"
        fluid
        icon={<History className="h-4 w-4" />}
        title={t('settings.skills.workflowActions.versionHistory')}
        subtitle={t('settings.skills.versioning.panelSubtitle', {
          defaultValue: '查看草稿与发布历史，并可恢复到当前草稿。',
        })}
        onClose={onClose}
        headerActions={(
          <>
            <button
              type="button"
              onClick={() => setClearConfirmOpen(true)}
              className="inline-flex items-center gap-1.5 rounded-2xl border border-slate-200 bg-white px-3 py-2 text-[11px] font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"
              title={t('settings.skills.versioning.clearNonPublished')}
              disabled={loading || clearing || versions.length === 0}
            >
              <Eraser className={`h-3.5 w-3.5 ${clearing ? 'animate-spin' : ''}`} />
              {t('settings.skills.versioning.clearNonPublished')}
            </button>
            <button
              type="button"
              onClick={onRefresh}
              className="inline-flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-800"
              title={t('common.refresh')}
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </>
        )}
        bodyClassName="min-h-0 flex-1 overflow-auto bg-slate-50/60"
      >
        <div className="border-b bg-white/90 px-5 py-4">
          <div className="text-xs text-muted-foreground">{t('settings.skills.versioning.currentDraft')}</div>
          <div className="text-sm font-medium mt-1">{draftVersionId ? draftVersionId.slice(0, 8) : '-'}</div>
        </div>

        <div className="space-y-2 p-4">
          {loadError && (
            <div className="rounded-2xl border border-red-200 bg-red-50 p-3 text-xs text-red-700">
              {t('settings.skills.versioning.loadFailed')}: {loadError}
            </div>
          )}

          {!loading && versions.length === 0 && (
            <div className="rounded-2xl border border-dashed p-6 text-center text-sm text-muted-foreground">
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
                  rounded-2xl border p-4 space-y-3
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
                      className="inline-flex items-center gap-1 rounded-xl border border-red-200 px-2.5 py-1.5 text-xs text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50"
                    >
                      <Trash2 className={`w-3 h-3 ${deleting ? 'animate-spin' : ''}`} />
                      {t('settings.skills.versioning.delete')}
                    </button>
                    <button
                      type="button"
                      onClick={() => onRestore(item.id)}
                      disabled={isDraft || restoring || deleting}
                      className="inline-flex items-center gap-1 rounded-xl border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700 transition-colors hover:bg-slate-50 disabled:opacity-50"
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
      </WorkflowEditorSurfaceShell>

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
    </>
  )
}
