import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AssistantTargetsSettings } from './AssistantTargetsSettings'
import * as queries from '../queries'

vi.mock('../queries', () => ({
  useAgentProfilesQuery: vi.fn(),
  useAgentProfileDetailQuery: vi.fn(),
  useCopyAgentProfileMutation: vi.fn(),
  useCopyWorkflowMutation: vi.fn(),
  useCreateAgentProfileMutation: vi.fn(),
  useCreateTargetFolderMutation: vi.fn(),
  useCreateWorkflowMutation: vi.fn(),
  useDeleteAgentProfileMutation: vi.fn(),
  useDeleteTargetFolderMutation: vi.fn(),
  useDeleteWorkflowMutation: vi.fn(),
  useMoveTargetFolderMutation: vi.fn(),
  useMoveTargetToFolderMutation: vi.fn(),
  useSkillsQuery: vi.fn(),
  useTargetFoldersQuery: vi.fn(),
  useUpdateTargetFolderMutation: vi.fn(),
  useWorkflowDetailQuery: vi.fn(),
  useWorkflowsQuery: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../components/AssistantFolderCard', () => ({
  AssistantFolderCard: ({ folder, onOpen }: { folder: { name: string }; onOpen: () => void }) => (
    <button type="button" onClick={onOpen}>{folder.name}</button>
  ),
}))

vi.mock('../components/AssistantTargetCard', () => ({
  AssistantTargetCard: ({ target }: { target: { name: string } }) => <div>{target.name}</div>,
}))

const mutation = () => ({ isPending: false, mutate: vi.fn(), mutateAsync: vi.fn() })

describe('AssistantTargetsSettings', () => {
  beforeEach(() => {
    vi.mocked(queries.useWorkflowsQuery).mockReturnValue({
      data: [
        {
          id: 'child-target',
          name: 'Child target',
          description: '',
          folderId: 'needle-folder',
          enabled: true,
          isSystem: false,
          hidden: false,
          referencedBySkillCount: 0,
          referencedBySkillNames: [],
          referencedSystemBehaviorKeys: [],
          systemBehaviorReferenceCount: 0,
          openclawReferenceCount: 0,
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-01T00:00:00Z',
        },
        {
          id: 'unrelated-target',
          name: 'Unrelated search result',
          description: 'needle',
          folderId: null,
          enabled: true,
          isSystem: false,
          hidden: false,
          referencedBySkillCount: 0,
          referencedBySkillNames: [],
          referencedSystemBehaviorKeys: [],
          systemBehaviorReferenceCount: 0,
          openclawReferenceCount: 0,
          createdAt: '2026-01-01T00:00:00Z',
          updatedAt: '2026-01-01T00:00:00Z',
        },
      ],
      isLoading: false,
    } as never)
    vi.mocked(queries.useAgentProfilesQuery).mockReturnValue({ data: [], isLoading: false } as never)
    vi.mocked(queries.useSkillsQuery).mockReturnValue({ data: [] } as never)
    vi.mocked(queries.useTargetFoldersQuery).mockReturnValue({
      data: [{
        id: 'needle-folder',
        name: 'Needle folder',
        description: '',
        parentId: null,
        colorToken: 'slate',
        iconKey: 'folder',
        path: [{ id: 'needle-folder', name: 'Needle folder' }],
        folderCount: 0,
        workflowCount: 1,
        agentCount: 0,
        directFolderCount: 0,
        directWorkflowCount: 1,
        directAgentCount: 0,
        lastActivityAt: '2026-01-01T00:00:00Z',
        createdAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z',
      }],
      isLoading: false,
    } as never)
    vi.mocked(queries.useWorkflowDetailQuery).mockReturnValue({ data: undefined, isLoading: false } as never)
    vi.mocked(queries.useAgentProfileDetailQuery).mockReturnValue({ data: undefined, isLoading: false } as never)

    ;[
      queries.useCopyAgentProfileMutation,
      queries.useCopyWorkflowMutation,
      queries.useCreateAgentProfileMutation,
      queries.useCreateTargetFolderMutation,
      queries.useCreateWorkflowMutation,
      queries.useDeleteAgentProfileMutation,
      queries.useDeleteTargetFolderMutation,
      queries.useDeleteWorkflowMutation,
      queries.useMoveTargetFolderMutation,
      queries.useMoveTargetToFolderMutation,
      queries.useUpdateTargetFolderMutation,
    ].forEach((hook) => vi.mocked(hook).mockReturnValue(mutation() as never))
  })

  it('leaves global search when opening a folder from search results', () => {
    render(
      <MemoryRouter>
        <AssistantTargetsSettings />
      </MemoryRouter>,
    )

    fireEvent.change(screen.getByPlaceholderText('settings.skills.searchTargets'), {
      target: { value: 'needle' },
    })
    expect(screen.getByText('Unrelated search result')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Needle folder' }))

    expect(screen.getByText('Child target')).toBeInTheDocument()
    expect(screen.queryByText('Unrelated search result')).not.toBeInTheDocument()
  })
})
