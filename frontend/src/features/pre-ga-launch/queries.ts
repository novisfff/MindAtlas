import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  consumeLaunchCandidate,
  createLaunchCandidate,
  getLaunchStatus,
  getQualificationTarget,
  listLaunchCandidates,
  type ConsumeLaunchCandidateInput,
  type CreateLaunchCandidateInput,
  type LaunchCandidatesCursor,
} from './api/launch'

export const preGaLaunchKeys = {
  all: ['pre-ga-launch'] as const,
  status: () => [...preGaLaunchKeys.all, 'status'] as const,
  target: () => [...preGaLaunchKeys.all, 'target'] as const,
  candidates: () => [...preGaLaunchKeys.all, 'candidates'] as const,
}

function invalidateLaunchQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: preGaLaunchKeys.status() })
  void queryClient.invalidateQueries({ queryKey: preGaLaunchKeys.target() })
  void queryClient.invalidateQueries({ queryKey: preGaLaunchKeys.candidates() })
}

export function usePreGaLaunchStatusQuery() {
  return useQuery({ queryKey: preGaLaunchKeys.status(), queryFn: getLaunchStatus, refetchInterval: 15_000 })
}

export function usePreGaLaunchQualificationTargetQuery() {
  return useQuery({
    queryKey: preGaLaunchKeys.target(),
    queryFn: getQualificationTarget,
    refetchInterval: 30_000,
    retry: false,
  })
}

export function usePreGaLaunchCandidatesQuery() {
  return useInfiniteQuery({
    queryKey: preGaLaunchKeys.candidates(),
    queryFn: ({ pageParam }) => listLaunchCandidates(pageParam ?? undefined),
    initialPageParam: null as LaunchCandidatesCursor | null,
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  })
}

export function useCreatePreGaLaunchCandidateMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateLaunchCandidateInput) => createLaunchCandidate(input),
    onSuccess: () => invalidateLaunchQueries(queryClient),
  })
}

export function useConsumePreGaLaunchCandidateMutation() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ candidateId, input }: { candidateId: string; input: ConsumeLaunchCandidateInput }) =>
      consumeLaunchCandidate(candidateId, input),
    onSuccess: () => invalidateLaunchQueries(queryClient),
  })
}
