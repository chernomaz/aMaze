import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Database, Search } from 'lucide-react'
import { registryApi } from '@/api/registry'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { formatDate } from '@/lib/utils'

const TYPE_LABELS: Record<string, string> = {
  agent: 'Agent',
  mcp_tool: 'MCP Tool',
  mcp_server: 'MCP Server',
}

export default function RegistryPage() {
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('_all')
  const [healthFilter, setHealthFilter] = useState('_all')

  const { data: entries = [], isLoading, refetch } = useQuery({
    queryKey: ['registry', typeFilter, healthFilter],
    queryFn: () =>
      registryApi.list({
        capability_type: typeFilter !== '_all' ? typeFilter : undefined,
        is_healthy: healthFilter === 'healthy' ? true : healthFilter === 'unhealthy' ? false : undefined,
      }),
    refetchInterval: 15_000,
  })

  const filtered = entries.filter((e) =>
    !search || e.name.toLowerCase().includes(search.toLowerCase()) || e.description.toLowerCase().includes(search.toLowerCase()),
  )

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Registry</h1>
          <p className="text-sm text-muted-foreground">Registered agents and MCP tools with health status</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>{filtered.length} of {entries.length} entries</span>
          <button onClick={() => refetch()} className="text-primary hover:underline">Refresh</button>
        </div>
      </div>

      {/* Filters */}
      <div className="mb-4 flex gap-3">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name…"
            className="pl-8 h-8"
          />
        </div>
        <Select value={typeFilter} onValueChange={setTypeFilter}>
          <SelectTrigger className="w-36 h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="_all">All types</SelectItem>
            <SelectItem value="agent">Agents</SelectItem>
            <SelectItem value="mcp_tool">MCP Tools</SelectItem>
            <SelectItem value="mcp_server">MCP Servers</SelectItem>
          </SelectContent>
        </Select>
        <Select value={healthFilter} onValueChange={setHealthFilter}>
          <SelectTrigger className="w-32 h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="_all">All health</SelectItem>
            <SelectItem value="healthy">Healthy</SelectItem>
            <SelectItem value="unhealthy">Unhealthy</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
          <Database className="h-12 w-12 opacity-30" />
          <p>{entries.length === 0 ? 'No registered capabilities.' : 'No results match the current filters.'}</p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((e) => (
            <Card key={e.id} className="border-border">
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className="font-semibold truncate">{e.name}</p>
                      <Badge variant={e.is_healthy ? 'success' : 'destructive'} className="shrink-0">
                        {e.is_healthy ? 'healthy' : 'unhealthy'}
                      </Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground truncate">{e.description || 'No description'}</p>
                  </div>
                  <Badge variant="secondary" className="shrink-0 text-xs">
                    {TYPE_LABELS[e.capability_type] ?? e.capability_type}
                  </Badge>
                </div>

                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  <div className="flex justify-between">
                    <span>Endpoint</span>
                    <span className="font-mono">{e.internal_host}:{e.internal_port}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Version</span>
                    <span>{e.version}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Last heartbeat</span>
                    <span>{formatDate(e.last_heartbeat)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Registered</span>
                    <span>{formatDate(e.registered_at)}</span>
                  </div>
                </div>

                {e.tags.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {e.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="text-xs px-1.5 py-0">{tag}</Badge>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
