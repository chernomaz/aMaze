import { apiFetch } from './client'
import type { PolicyResponse, PolicyCreate, PolicyUpdate } from '@/types'

export const policiesApi = {
  list: () => apiFetch<PolicyResponse[]>('/policies'),
  get: (id: string) => apiFetch<PolicyResponse>(`/policies/${id}`),
  create: (body: PolicyCreate) => apiFetch<PolicyResponse>('/policies', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  update: (id: string, body: PolicyUpdate) => apiFetch<PolicyResponse>(`/policies/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }),
  delete: (id: string) => apiFetch<void>(`/policies/${id}`, { method: 'DELETE' }),
}
