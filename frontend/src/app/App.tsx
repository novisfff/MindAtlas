import { lazy, Suspense, type ComponentType, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AppProviders } from './providers'
import { AppLayout } from '@/components/layout'
import { InitializationGate } from '@/features/initialization'
import { OperatorGate, OperatorLoginPage } from '@/features/operator-auth'

function lazyNamed<TModule extends Record<string, ComponentType<any>>, TKey extends keyof TModule>(
  loader: () => Promise<TModule>,
  exportName: TKey,
) {
  return lazy(async () => {
    const mod = await loader()
    return { default: mod[exportName] }
  })
}

const EntriesPage = lazy(() => import('@/features/entries/EntriesPage'))
const EntryDetailPage = lazyNamed(() => import('@/features/entries/EntryDetailPage'), 'EntryDetailPage')
const EntryNewPage = lazyNamed(() => import('@/features/entries/EntryNewPage'), 'EntryNewPage')
const EntryEditPage = lazyNamed(() => import('@/features/entries/EntryEditPage'), 'EntryEditPage')
const GraphPage = lazyNamed(() => import('@/features/graph/GraphPage'), 'GraphPage')
const DashboardPage = lazyNamed(() => import('@/features/dashboard/DashboardPage'), 'DashboardPage')
const CalendarPage = lazyNamed(() => import('@/features/calendar/CalendarPage'), 'CalendarPage')
const SettingsPage = lazyNamed(() => import('@/features/settings/SettingsPage'), 'SettingsPage')
const AssistantRuntimeSettingsPage = lazyNamed(
  () => import('@/features/settings/pages/AssistantRuntimeSettings'),
  'AssistantRuntimeSettingsPage',
)
const PreGaLaunchPage = lazyNamed(
  () => import('@/features/pre-ga-launch/pages/PreGaLaunchPage'),
  'PreGaLaunchPage',
)
const ReconciliationPage = lazyNamed(
  () => import('@/features/reconciliation/pages/ReconciliationPage'),
  'ReconciliationPage',
)
const EntryTypeSettings = lazyNamed(() => import('@/features/settings/pages/EntryTypeSettings'), 'EntryTypeSettings')
const TagSettings = lazyNamed(() => import('@/features/settings/pages/TagSettings'), 'TagSettings')
const AiProviderSettings = lazyNamed(() => import('@/features/ai-providers/pages/AiProviderSettings'), 'AiProviderSettings')
const OpenClawIntegrationSettingsPage = lazyNamed(
  () => import('@/features/settings/pages/OpenClawIntegrationSettings'),
  'OpenClawIntegrationSettingsPage',
)
const SystemSetupSettingsPage = lazyNamed(
  () => import('@/features/settings/pages/SystemSetupSettings'),
  'SystemSetupSettingsPage',
)
const AutomationSettingsPage = lazyNamed(
  () => import('@/features/settings/pages/AutomationSettings'),
  'AutomationSettingsPage',
)
const LightRagSettingsPage = lazyNamed(() => import('@/features/settings/pages/LightRagSettings'), 'LightRagSettingsPage')
const DoclingSettingsPage = lazyNamed(() => import('@/features/settings/pages/DoclingSettings'), 'DoclingSettingsPage')
const ToolSettings = lazyNamed(() => import('@/features/assistant-config/pages/ToolSettings'), 'ToolSettings')
const UniversalSkillSettings = lazyNamed(() => import('@/features/assistant-config/pages/UniversalSkillSettings'), 'UniversalSkillSettings')
const UniversalSkillEditorPage = lazyNamed(() => import('@/features/assistant-config/pages/UniversalSkillEditorPage'), 'UniversalSkillEditorPage')
const MainAgentProfileEditorPage = lazyNamed(() => import('@/features/assistant-config/pages/MainAgentProfileEditorPage'), 'MainAgentProfileEditorPage')
const Plan09RouteGate = lazyNamed(
  () => import('@/features/assistant-config/components/Plan09RouteGate'),
  'Plan09RouteGate',
)
const AssistantTargetsSettings = lazyNamed(
  () => import('@/features/assistant-config/pages/AssistantTargetsSettings'),
  'AssistantTargetsSettings',
)
const SystemAiBehaviorsSettings = lazyNamed(
  () => import('@/features/assistant-config/pages/SystemAiBehaviorsSettings'),
  'SystemAiBehaviorsSettings',
)
const AssistantPage = lazy(() => import('@/features/assistant/AssistantPage'))
const SystemInitializationPage = lazyNamed(
  () => import('@/features/initialization/pages/SystemInitializationPage'),
  'SystemInitializationPage',
)

const WorkflowEditorPage = lazy(
  () => import('@/features/assistant-config/pages/WorkflowEditorPage'),
)
const AgentEditorPage = lazy(
  () => import('@/features/assistant-config/pages/AgentEditorPage'),
)

export default function App() {
  const { t } = useTranslation()

  const pageFallback = (
    <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
      {t('messages.loading')}
    </div>
  )

  const settingsEditorFallback = (
    <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
      {t('messages.loading')}
    </div>
  )

  function withPageFallback(element: ReactNode) {
    return <Suspense fallback={pageFallback}>{element}</Suspense>
  }

  return (
    <AppProviders>
      <BrowserRouter>
        <InitializationGate>
          <OperatorGate>
          <Routes>
            <Route path="/initialize" element={withPageFallback(<SystemInitializationPage />)} />
            <Route path="/login" element={withPageFallback(<OperatorLoginPage />)} />
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route
              path="/settings/workflow-editor/:workflowId"
              element={
                <Suspense fallback={settingsEditorFallback}>
                  <WorkflowEditorPage />
                </Suspense>
              }
            />
            <Route
              path="/settings/agent-editor/:agentProfileId"
              element={
                <Suspense fallback={settingsEditorFallback}>
                  <AgentEditorPage />
                </Suspense>
              }
            />
            <Route element={<AppLayout />}>
              <Route path="/dashboard" element={withPageFallback(<DashboardPage />)} />
              <Route path="/entries" element={withPageFallback(<EntriesPage />)} />
              <Route path="/entries/new" element={withPageFallback(<EntryNewPage />)} />
              <Route path="/entries/:id" element={withPageFallback(<EntryDetailPage />)} />
              <Route path="/entries/:id/edit" element={withPageFallback(<EntryEditPage />)} />
              <Route path="/graph" element={withPageFallback(<GraphPage />)} />
              <Route path="/calendar" element={withPageFallback(<CalendarPage />)} />
              <Route path="/settings" element={withPageFallback(<SettingsPage />)} />
              <Route
                path="/settings/assistant-runtime"
                element={withPageFallback(<AssistantRuntimeSettingsPage />)}
              />
              <Route path="/settings/pre-ga-launch" element={withPageFallback(<PreGaLaunchPage />)} />
              <Route path="/settings/reconciliation" element={withPageFallback(<ReconciliationPage />)} />
              <Route path="/settings/entry-types" element={withPageFallback(<EntryTypeSettings />)} />
              <Route path="/settings/tags" element={withPageFallback(<TagSettings />)} />
              <Route path="/settings/ai-providers" element={withPageFallback(<AiProviderSettings />)} />
              <Route
                path="/settings/openclaw-integration"
                element={withPageFallback(<OpenClawIntegrationSettingsPage />)}
              />
              <Route path="/settings/system-setup" element={withPageFallback(<SystemSetupSettingsPage />)} />
              <Route path="/settings/automation" element={withPageFallback(<AutomationSettingsPage />)} />
              <Route path="/settings/lightrag" element={withPageFallback(<LightRagSettingsPage />)} />
              <Route path="/settings/docling" element={withPageFallback(<DoclingSettingsPage />)} />
              <Route path="/settings/assistant-tools" element={withPageFallback(<ToolSettings />)} />
              {/* Deploy B1: legacy Skill Library unmounted; redirect old bookmarks. */}
              <Route path="/settings/assistant-skills" element={<Navigate to="/settings/universal-skills" replace />} />
              <Route
                path="/settings/universal-skills"
                element={withPageFallback(
                  <Plan09RouteGate>
                    <UniversalSkillSettings />
                  </Plan09RouteGate>,
                )}
              />
              <Route
                path="/settings/universal-skills/:packageId"
                element={withPageFallback(
                  <Plan09RouteGate>
                    <UniversalSkillEditorPage />
                  </Plan09RouteGate>,
                )}
              />
              <Route
                path="/settings/main-agent-profile"
                element={withPageFallback(
                  <Plan09RouteGate titleKey="settings.universalSkills.profileTitle">
                    <MainAgentProfileEditorPage />
                  </Plan09RouteGate>,
                )}
              />
              <Route
                path="/settings/assistant-targets"
                element={withPageFallback(<AssistantTargetsSettings />)}
              />
              <Route
                path="/settings/system-ai-behaviors"
                element={withPageFallback(<SystemAiBehaviorsSettings />)}
              />
              <Route path="/settings/assistant-workflows" element={<Navigate to="/settings/assistant-targets" replace />} />
              <Route path="/settings/assistant-agents" element={<Navigate to="/settings/assistant-targets" replace />} />
              <Route path="/assistant" element={withPageFallback(<AssistantPage />)} />
            </Route>
          </Routes>
          </OperatorGate>
        </InitializationGate>
      </BrowserRouter>
    </AppProviders>
  )
}
