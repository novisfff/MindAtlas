import { useMutation } from '@tanstack/react-query'
import { generateAiContent, type AiGenerateRequest } from './api/ai'

export function useAiGenerateMutation() {
  return useMutation({
    mutationFn: (request: AiGenerateRequest) => generateAiContent(request),
  })
}
