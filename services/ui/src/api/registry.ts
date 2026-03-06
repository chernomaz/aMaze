import { apiFetch } from './client'
import type { RegistryEntry } from '@/types'

export const registryApi = {
  list: (params?: { capability_type?: string; tag?: string; is_healthy?: boolean; name?: string }) => {
    const qs = new URLSearchParams()
    if (params?.capability_type) qs.set('capability_type', params.capability_type)
    if (params?.tag) qs.set('tag', params.tag)
    if (params?.is_healthy !== undefined) qs.set('is_healthy', String(params.is_healthy))
    if (params?.name) qs.set('name', params.name)
    const q = qs.toString()
    return apiFetch<RegistryEntry[]>(`/registry/capabilities${q ? `?${q}` : ''}`)
  },
  get: (name: string) => apiFetch<RegistryEntry>(`/registry/capabilities/${name}`),
}
