import type { PropsWithChildren } from 'react'
import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDeleteTargetFolderMutation, useMoveTargetToFolderMutation } from './queries'
import * as targetFoldersApi from './api/target-folders'

vi.mock('./api/target-folders', () => ({
  deleteTargetFolder: vi.fn(),
  moveTargetToFolder: vi.fn(),
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('assistant target folder query invalidation', () => {
  beforeEach(() => {
    vi.mocked(targetFoldersApi.deleteTargetFolder).mockReset().mockResolvedValue(undefined)
    vi.mocked(targetFoldersApi.moveTargetToFolder).mockReset().mockResolvedValue(undefined)
  })

  it('invalidates cached target details when deleting a folder', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(['assistant-workflow', 'workflow-1'], { folderId: 'folder-1' })
    queryClient.setQueryData(['assistant-agent-profile', 'agent-1'], { folderId: 'folder-1' })
    const { result } = renderHook(() => useDeleteTargetFolderMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync('folder-1')
    })

    expect(queryClient.getQueryState(['assistant-workflow', 'workflow-1'])?.isInvalidated).toBe(true)
    expect(queryClient.getQueryState(['assistant-agent-profile', 'agent-1'])?.isInvalidated).toBe(true)
  })

  it('invalidates the moved target detail cache', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    queryClient.setQueryData(['assistant-workflow', 'workflow-1'], { folderId: null })
    const { result } = renderHook(() => useMoveTargetToFolderMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        targetType: 'workflow',
        targetId: 'workflow-1',
        folderId: 'folder-1',
      })
    })

    expect(queryClient.getQueryState(['assistant-workflow', 'workflow-1'])?.isInvalidated).toBe(true)
  })
})
