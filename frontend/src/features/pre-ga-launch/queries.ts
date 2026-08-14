import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  consumeLaunchCandidate,
  createLaunchCandidate,
  getLaunchStatus,
  listLaunchCandidates,
  type ConsumeLaunchCandidateInput,
  type CreateLaunchCandidateInput,
} from './api/launch'

export const preGaLaunchKeys = {
  all: ['pre-ga-launch'] as const,
  status: () => [...preGaLaunchKeys.all, 'status'] as const,
  candidates: () => [...preGaLaunchKeys.all, 'candidates'] as const,
}

function invalidateLaunchQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: preGaLaunchKeys.status() })
  void queryClient.invalidateQueries({ queryKey: preGaLaunchKeys.candidates() })
}

export function usePreGaLaunchStatusQuery() {
  return useQuery({ queryKey: preGaLaunchKeys.status(), queryFn: getLaunchStatus, refetchInterval: 15_000 })
}

export function usePreGaLaunchCandidatesQuery() {
  return useQuery({ queryKey: preGaLaunchKeys.candidates(), queryFn: listLaunchCandidates })
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
