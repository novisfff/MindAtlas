import { apiClient } from '@/lib/api/client'

export interface OperatorSession {
  authenticated: boolean
  role?: 'operator'
  idleExpiresAt?: string
  absoluteExpiresAt?: string
}

export function getOperatorSession(): Promise<OperatorSession> {
  return apiClient.get<OperatorSession>('/api/operator-auth/session')
}

export function loginOperator(password: string): Promise<OperatorSession> {
  return apiClient.post<OperatorSession>('/api/operator-auth/login', {
    body: { password },
  })
}

export function logoutOperator(): Promise<{ loggedOut: true }> {
  return apiClient.post('/api/operator-auth/logout', { body: {} })
}
