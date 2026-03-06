import { apiFetch } from './client'
import type { GraphResponse, GraphCreate, GraphUpdate } from '@/types'

export const graphsApi = {
  list: () => apiFetch<GraphResponse[]>('/graphs'),
  get: (id: string) => apiFetch<GraphResponse>(`/graphs/${id}`),
  create: (body: GraphCreate) => apiFetch<GraphResponse>('/graphs', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  update: (id: string, body: GraphUpdate) => apiFetch<GraphResponse>(`/graphs/${id}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  }),
  delete: (id: string) => apiFetch<void>(`/graphs/${id}`, { method: 'DELETE' }),
}
