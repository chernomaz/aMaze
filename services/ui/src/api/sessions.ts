import { apiFetch } from './client'
import type { SessionResponse, SessionEventResponse, SessionCreate } from '@/types'

export const sessionsApi = {
  list: (params?: { status?: string; agent_id?: string }) => {
    const qs = new URLSearchParams()
    if (params?.status) qs.set('status', params.status)
    if (params?.agent_id) qs.set('agent_id', params.agent_id)
    const q = qs.toString()
    return apiFetch<SessionResponse[]>(`/sessions${q ? `?${q}` : ''}`)
  },
  get: (id: string) => apiFetch<SessionResponse>(`/sessions/${id}`),
  events: (id: string) => apiFetch<SessionEventResponse[]>(`/sessions/${id}/events`),
  create: (body: SessionCreate) => apiFetch<SessionResponse>('/sessions', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  abort: (id: string) => apiFetch<void>(`/sessions/${id}`, { method: 'DELETE' }),
}
