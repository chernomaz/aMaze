import { apiFetch } from './client'
import type { AgentResponse, AgentCreate, AgentUpdate } from '@/types'

export const agentsApi = {
  list: () => apiFetch<AgentResponse[]>('/agents'),
  get: (id: string) => apiFetch<AgentResponse>(`/agents/${id}`),
  create: (body: AgentCreate) => apiFetch<AgentResponse>('/agents', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  update: (id: string, body: AgentUpdate) => apiFetch<AgentResponse>(`/agents/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }),
  delete: (id: string) => apiFetch<void>(`/agents/${id}`, { method: 'DELETE' }),
}
