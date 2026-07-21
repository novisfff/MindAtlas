/**
 * Main Agent Profile editor (Plan 09 Task 7).
 * Prompt layers, catalog scope, control capabilities, budgets, versions.
 * Must not embed a single Skill as the execution target.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { uiField } from '@/components/ui/styles'
import {
  SettingsPageHeader,
  SettingsPageShell,
  SettingsSection,
} from '@/features/settings/components/SettingsShell'
import { cn } from '@/lib/utils'

import {
  assertNoSingleTargetFields,
  getDefaultMainAgentProfile,
  getDefaultMainAgentVersion,
  listDefaultMainAgentVersions,
  publishDefaultMainAgent,
  saveDefaultMainAgentDraft,
  type MainAgentProfileSnapshot,
  type MainAgentProfileSummary,
  type MainAgentProfileVersionSummary,
} from '../api/main-agent-profiles'
import { mapSkillPackageError, newRequestId } from '../api/skill-packages'
import { useSkillAdminSurfaceQuery } from '../queries'

const DEFAULT_SNAPSHOT: MainAgentProfileSnapshot = {
  schemaVersion: 1,
  basePrompt:
    'You are the MindAtlas main assistant. Answer directly when no specialized Skill is required.',
  responseStyle: {},
  supportedEntrypoints: ['assistant_chat'],
  modelRequirements: {
    toolCalling: true,
    streaming: true,
    multiToolCalls: true,
    jsonSchema: true,
  },
  controlCapabilityKeys: [],
  skillCatalogScope: { mode: 'all_published', packageIds: [] },
  contextBudget: {
    maxPromptCharacters: 72000,
    maxActiveSkills: 4,
    maxSkillInstructionCharacters: 24000,
    maxSingleSkillInstructionCharacters: 12000,
    maxHistoryCharacters: 24000,
    maxToolSummaryCharacters: 24000,
    maxResourceBytesPerCall: 65536,
  },
  outputBudget: {
    maxCompletionTokens: 4096,
    maxProviderRounds: 8,
    maxOuterAgentRounds: 8,
    maxTotalCapabilityCalls: 16,
    maxParallelCalls: 4,
    maxCapabilityDepth: 4,
    maxAgentDepth: 2,
    maxSameReadSignature: 3,
    maxCompletionFollowupRounds: 2,
    maxWallTimeMs: 120000,
  },
  globalSafetyPolicy: { denyByDefault: true },
  fallbackPolicy: { legacyRuntimeAllowed: true, beforeSideEffectsOnly: true },
}

export function MainAgentProfileEditorPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const surface = useSkillAdminSurfaceQuery()
  const [profile, setProfile] = useState<MainAgentProfileSummary | null>(null)
  const [versions, setVersions] = useState<MainAgentProfileVersionSummary[]>([])
  const [snapshot, setSnapshot] = useState<MainAgentProfileSnapshot>(DEFAULT_SNAPSHOT)
  const [controlKeysText, setControlKeysText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const singleTargetIssues = useMemo(
    () => assertNoSingleTargetFields(snapshot as unknown as Record<string, unknown>),
    [snapshot],
  )

  async function reload() {
    setError(null)
    const summary = await getDefaultMainAgentProfile()
    setProfile(summary)
    const page = await listDefaultMainAgentVersions({ limit: 50, offset: 0 })
    setVersions(page.items || [])
    // Load the actual draft/published snapshot — never save DEFAULT_SNAPSHOT over server state.
    const versionId = summary.draftVersion?.id || summary.publishedVersion?.id
    if (versionId) {
      const detail = await getDefaultMainAgentVersion(versionId)
      const snap = detail.snapshot as MainAgentProfileSnapshot
      if (snap && typeof snap === 'object' && snap.basePrompt) {
        setSnapshot({
          ...DEFAULT_SNAPSHOT,
          ...snap,
          schemaVersion: 1,
          controlCapabilityKeys: snap.controlCapabilityKeys || [],
          skillCatalogScope: snap.skillCatalogScope || DEFAULT_SNAPSHOT.skillCatalogScope,
          modelRequirements: snap.modelRequirements || DEFAULT_SNAPSHOT.modelRequirements,
          contextBudget: snap.contextBudget || DEFAULT_SNAPSHOT.contextBudget,
          outputBudget: snap.outputBudget || DEFAULT_SNAPSHOT.outputBudget,
          globalSafetyPolicy: snap.globalSafetyPolicy || DEFAULT_SNAPSHOT.globalSafetyPolicy,
          fallbackPolicy: snap.fallbackPolicy || DEFAULT_SNAPSHOT.fallbackPolicy,
          responseStyle: snap.responseStyle || {},
          supportedEntrypoints: snap.supportedEntrypoints || ['assistant_chat'],
        })
        setControlKeysText((snap.controlCapabilityKeys || []).join(', '))
        setDirty(false)
      }
    }
  }

  useEffect(() => {
    void reload().catch((err) => setError(mapSkillPackageError(err).message))
  }, [])

  if (surface.isLoading) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.profileTitle')}
          description={t('messages.loading')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
      </SettingsPageShell>
    )
  }

  if (!surface.data?.available) {
    return (
      <SettingsPageShell>
        <SettingsPageHeader
          title={t('settings.universalSkills.profileTitle')}
          description={t('settings.universalSkills.unavailableDesc')}
          backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
        />
      </SettingsPageShell>
    )
  }

  async function handleSave() {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      const keys = controlKeysText
        .split(/[\s,]+/)
        .map((k) => k.trim())
        .filter(Boolean)
      const next: MainAgentProfileSnapshot = {
        ...snapshot,
        controlCapabilityKeys: keys,
      }
      if (assertNoSingleTargetFields(next as unknown as Record<string, unknown>).length) {
        throw new Error(t('settings.universalSkills.profileNoSingleTarget'))
      }
      const version = await saveDefaultMainAgentDraft({ snapshot: next })
      setDirty(false)
      setMessage(`${t('settings.universalSkills.saveDraft')}: ${version.id}`)
      await reload()
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(false)
    }
  }

  async function handlePublish() {
    if (!profile?.draftVersion?.id) {
      setError(t('settings.universalSkills.noDraftVersion'))
      return
    }
    setBusy(true)
    setError(null)
    try {
      await publishDefaultMainAgent({
        draftVersionId: profile.draftVersion.id,
        expectedAggregateRevision: profile.aggregateRevision ?? 0,
        requestId: newRequestId('profile-pub'),
      })
      setMessage(t('settings.universalSkills.profilePublished'))
      await reload()
    } catch (err) {
      setError(mapSkillPackageError(err).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsPageShell>
      <SettingsPageHeader
        title={t('settings.universalSkills.profileTitle')}
        description={profile?.profileKey || 'default'}
        backAction={{ label: t('common.back'), onClick: () => navigate('/settings') }}
      />
      <SettingsSection className="space-y-4">
        {error ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
            {error}
          </div>
        ) : null}
        {message ? <div className="rounded-md border p-3 text-sm">{message}</div> : null}
        {singleTargetIssues.length > 0 ? (
          <div role="alert" className="rounded-md border border-destructive/40 p-3 text-sm">
            {t('settings.universalSkills.profileNoSingleTarget')}: {singleTargetIssues.join(', ')}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-full border px-2 py-0.5">
            runtime={profile?.runtimeEnabled ? 'enabled' : 'disabled'}
          </span>
          <span className="rounded-full border px-2 py-0.5">
            draft={profile?.draftVersion?.id ?? '—'}
          </span>
          <span className="rounded-full border px-2 py-0.5">
            published={profile?.publishedVersion?.id ?? '—'}
          </span>
          {dirty ? (
            <span className="rounded-full bg-blue-500/10 px-2 py-0.5 text-blue-700 dark:text-blue-300">
              {t('settings.universalSkills.dirty')}
            </span>
          ) : null}
        </div>

        <label className="block space-y-1 text-sm">
          <span>{t('settings.universalSkills.basePrompt')}</span>
          <textarea
            className={cn(uiField.textarea, 'min-h-[180px] font-mono text-xs')}
            value={snapshot.basePrompt}
            onChange={(e) => {
              setSnapshot((s) => ({ ...s, basePrompt: e.target.value }))
              setDirty(true)
            }}
          />
        </label>

        <label className="block space-y-1 text-sm">
          <span>{t('settings.universalSkills.controlCapabilities')}</span>
          <input
            className={uiField.input}
            value={controlKeysText}
            onChange={(e) => {
              setControlKeysText(e.target.value)
              setDirty(true)
            }}
            placeholder="capability.keys,comma,separated"
          />
        </label>

        <label className="block space-y-1 text-sm">
          <span>{t('settings.universalSkills.catalogScopeMode')}</span>
          <select
            className={uiField.select}
            value={snapshot.skillCatalogScope.mode}
            onChange={(e) => {
              setSnapshot((s) => ({
                ...s,
                skillCatalogScope: {
                  ...s.skillCatalogScope,
                  mode: e.target.value as 'all_published' | 'allowlist',
                },
              }))
              setDirty(true)
            }}
          >
            <option value="all_published">all_published</option>
            <option value="allowlist">allowlist</option>
          </select>
        </label>

        <div className="flex flex-wrap gap-2">
          <Button type="button" disabled={busy || !dirty} onClick={() => void handleSave()}>
            {t('settings.universalSkills.saveDraft')}
          </Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void handlePublish()}>
            {t('settings.universalSkills.publishProfile')}
          </Button>
        </div>

        <div className="space-y-2">
          <h3 className="text-sm font-medium">{t('settings.universalSkills.versionHistory')}</h3>
          <ul className="divide-y rounded-md border">
            {versions.map((v) => (
              <li key={v.id} className="p-3 text-sm">
                <div className="font-medium">
                  #{v.sequenceNo} {v.versionName} · {v.versionSource}
                </div>
                <div className="font-mono text-xs text-muted-foreground">
                  {v.id} · {v.contentDigest.slice(0, 16)}…
                </div>
              </li>
            ))}
            {versions.length === 0 ? (
              <li className="p-3 text-sm text-muted-foreground">{t('settings.universalSkills.noVersions')}</li>
            ) : null}
          </ul>
        </div>
      </SettingsSection>
    </SettingsPageShell>
  )
}
