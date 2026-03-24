import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { AppProviders } from './providers'
import { AppLayout } from '@/components/layout'
import { EntriesPage, EntryDetailPage, EntryNewPage, EntryEditPage } from '@/features/entries'
import { GraphPage } from '@/features/graph'
import { DashboardPage } from '@/features/dashboard'
import { CalendarPage } from '@/features/calendar'
import { SettingsPage, EntryTypeSettings, TagSettings } from '@/features/settings'
import { AiProviderSettings } from '@/features/ai-providers'
import { AssistantPage } from '@/features/assistant'
import { ToolSettings, SkillSettings, AssistantTargetsSettings, SystemAiBehaviorsSettings } from '@/features/assistant-config'

const WorkflowEditorPage = lazy(
  () => import('@/features/assistant-config/pages/WorkflowEditorPage'),
)
const AgentEditorPage = lazy(
  () => import('@/features/assistant-config/pages/AgentEditorPage'),
)

export default function App() {
  const { t } = useTranslation()

  const settingsEditorFallback = (
    <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
      {t('messages.loading')}
    </div>
  )

  return (
    <AppProviders>
      <BrowserRouter>
        <Routes>
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
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/entries" element={<EntriesPage />} />
            <Route path="/entries/new" element={<EntryNewPage />} />
            <Route path="/entries/:id" element={<EntryDetailPage />} />
            <Route path="/entries/:id/edit" element={<EntryEditPage />} />
            <Route path="/graph" element={<GraphPage />} />
            <Route path="/calendar" element={<CalendarPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/settings/entry-types" element={<EntryTypeSettings />} />
            <Route path="/settings/tags" element={<TagSettings />} />
            <Route path="/settings/ai-providers" element={<AiProviderSettings />} />
            <Route path="/settings/assistant-tools" element={<ToolSettings />} />
            <Route path="/settings/assistant-skills" element={<SkillSettings />} />
            <Route path="/settings/assistant-targets" element={<AssistantTargetsSettings />} />
            <Route path="/settings/system-ai-behaviors" element={<SystemAiBehaviorsSettings />} />
            <Route path="/settings/assistant-workflows" element={<Navigate to="/settings/assistant-targets" replace />} />
            <Route path="/settings/assistant-agents" element={<Navigate to="/settings/assistant-targets" replace />} />
            <Route path="/assistant" element={<AssistantPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProviders>
  )
}
