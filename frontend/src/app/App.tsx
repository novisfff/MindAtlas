import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AppProviders } from './providers'
import { AppLayout } from '@/components/layout'
import { EntriesPage, EntryDetailPage, EntryNewPage, EntryEditPage } from '@/features/entries'
import { GraphPage } from '@/features/graph'
import { DashboardPage } from '@/features/dashboard'
import { CalendarPage } from '@/features/calendar'
import { SettingsPage, EntryTypeSettings, TagSettings } from '@/features/settings'
import { AiProviderSettings } from '@/features/ai-providers'
import { AssistantPage } from '@/features/assistant'
import { ToolSettings, SkillSettings } from '@/features/assistant-config'

export default function App() {
  return (
    <AppProviders>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
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
            <Route path="/assistant" element={<AssistantPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProviders>
  )
}
