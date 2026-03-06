import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Bot } from 'lucide-react'
import { agentsApi } from '@/api/agents'
import { policiesApi } from '@/api/policies'
import type { AgentResponse, AgentCreate, AgentUpdate, MountInput } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'

const STATUS_COLORS: Record<string, 'default' | 'success' | 'destructive' | 'warning'> = {
  active: 'success',
  inactive: 'secondary',
  error: 'destructive',
  running: 'default',
}

type FormState = {
  name: string
  description: string
  image: string
  version: string
  capabilities: string
  required_capabilities: string
  env_vars: string
  secret_refs: string
  policy_id: string
  mem_limit: string
  cpu_quota: string
  mounts: MountInput[]
}

const defaultForm = (): FormState => ({
  name: '',
  description: '',
  image: '',
  version: 'latest',
  capabilities: '',
  required_capabilities: '',
  env_vars: '',
  secret_refs: '',
  policy_id: '',
  mem_limit: '2g',
  cpu_quota: '100000',
  mounts: [],
})

function agentToForm(a: AgentResponse): FormState {
  return {
    name: a.name,
    description: a.description,
    image: a.image,
    version: a.version,
    capabilities: a.capabilities.join(', '),
    required_capabilities: a.required_capabilities.join(', '),
    env_vars: Object.entries(a.env_vars).map(([k, v]) => `${k}=${v}`).join('\n'),
    secret_refs: a.secret_refs.join(', '),
    policy_id: a.policy_id ?? '',
    mem_limit: a.mem_limit,
    cpu_quota: String(a.cpu_quota),
    mounts: a.mounts.map((m) => ({ host_path: m.host_path, container_path: m.container_path, read_only: m.read_only })),
  }
}

function formToPayload(f: FormState): AgentCreate {
  const env_vars: Record<string, string> = {}
  f.env_vars.split('\n').forEach((line) => {
    const idx = line.indexOf('=')
    if (idx > 0) env_vars[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
  })
  return {
    name: f.name,
    description: f.description,
    image: f.image,
    version: f.version,
    capabilities: f.capabilities.split(',').map((s) => s.trim()).filter(Boolean),
    required_capabilities: f.required_capabilities.split(',').map((s) => s.trim()).filter(Boolean),
    env_vars,
    secret_refs: f.secret_refs.split(',').map((s) => s.trim()).filter(Boolean),
    policy_id: f.policy_id || null,
    mem_limit: f.mem_limit,
    cpu_quota: parseInt(f.cpu_quota) || 100000,
    mounts: f.mounts,
  }
}

export default function AgentsPage() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AgentResponse | null>(null)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [error, setError] = useState('')

  const { data: agents = [], isLoading } = useQuery({ queryKey: ['agents'], queryFn: agentsApi.list })
  const { data: policies = [] } = useQuery({ queryKey: ['policies'], queryFn: policiesApi.list })

  const createMut = useMutation({
    mutationFn: agentsApi.create,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agents'] }); closeDialog() },
    onError: (e: Error) => setError(e.message),
  })
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: AgentUpdate }) => agentsApi.update(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['agents'] }); closeDialog() },
    onError: (e: Error) => setError(e.message),
  })
  const deleteMut = useMutation({
    mutationFn: agentsApi.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  })

  function openCreate() {
    setEditing(null)
    setForm(defaultForm())
    setError('')
    setOpen(true)
  }
  function openEdit(a: AgentResponse) {
    setEditing(a)
    setForm(agentToForm(a))
    setError('')
    setOpen(true)
  }
  function closeDialog() {
    setOpen(false)
    setEditing(null)
  }

  function handleSubmit() {
    setError('')
    const payload = formToPayload(form)
    if (editing) {
      updateMut.mutate({ id: editing.id, body: payload })
    } else {
      createMut.mutate(payload)
    }
  }

  function addMount() {
    setForm((f) => ({ ...f, mounts: [...f.mounts, { host_path: '', container_path: '', read_only: false }] }))
  }
  function updateMount(i: number, field: keyof MountInput, value: string | boolean) {
    setForm((f) => {
      const mounts = [...f.mounts]
      mounts[i] = { ...mounts[i], [field]: value }
      return { ...f, mounts }
    })
  }
  function removeMount(i: number) {
    setForm((f) => ({ ...f, mounts: f.mounts.filter((_, idx) => idx !== i) }))
  }

  const isPending = createMut.isPending || updateMut.isPending

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Agents</h1>
          <p className="text-sm text-muted-foreground">Manage agent definitions and their configurations</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> New Agent
        </Button>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : agents.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
          <Bot className="h-12 w-12 opacity-30" />
          <p>No agents yet. Create one to get started.</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Image</TableHead>
              <TableHead>Capabilities</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Memory</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {agents.map((a) => (
              <TableRow key={a.id}>
                <TableCell>
                  <div>
                    <p className="font-medium">{a.name}</p>
                    {a.description && <p className="text-xs text-muted-foreground">{a.description}</p>}
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">{a.image}:{a.version}</TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {a.capabilities.slice(0, 3).map((c) => (
                      <Badge key={c} variant="secondary" className="text-xs">{c}</Badge>
                    ))}
                    {a.capabilities.length > 3 && (
                      <Badge variant="outline" className="text-xs">+{a.capabilities.length - 3}</Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant={STATUS_COLORS[a.status] ?? 'secondary'}>{a.status}</Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{a.mem_limit}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button variant="ghost" size="icon" onClick={() => openEdit(a)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      onClick={() => deleteMut.mutate(a.id)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? 'Edit Agent' : 'New Agent'}</DialogTitle>
          </DialogHeader>

          <div className="grid gap-4 py-2">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Name *</Label>
                <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="my-agent" />
              </div>
              <div className="space-y-1.5">
                <Label>Policy</Label>
                <Select value={form.policy_id} onValueChange={(v) => setForm((f) => ({ ...f, policy_id: v === '_none' ? '' : v })}>
                  <SelectTrigger><SelectValue placeholder="None" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_none">None</SelectItem>
                    {policies.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Description</Label>
              <Input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Docker Image *</Label>
                <Input value={form.image} onChange={(e) => setForm((f) => ({ ...f, image: e.target.value }))} placeholder="amaze/agent-base" />
              </div>
              <div className="space-y-1.5">
                <Label>Version</Label>
                <Input value={form.version} onChange={(e) => setForm((f) => ({ ...f, version: e.target.value }))} placeholder="latest" />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Capabilities <span className="text-muted-foreground">(comma-separated)</span></Label>
              <Input value={form.capabilities} onChange={(e) => setForm((f) => ({ ...f, capabilities: e.target.value }))} placeholder="llm, search, code" />
            </div>

            <div className="space-y-1.5">
              <Label>Required Capabilities <span className="text-muted-foreground">(comma-separated)</span></Label>
              <Input value={form.required_capabilities} onChange={(e) => setForm((f) => ({ ...f, required_capabilities: e.target.value }))} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Memory Limit</Label>
                <Input value={form.mem_limit} onChange={(e) => setForm((f) => ({ ...f, mem_limit: e.target.value }))} placeholder="2g" />
              </div>
              <div className="space-y-1.5">
                <Label>CPU Quota</Label>
                <Input type="number" value={form.cpu_quota} onChange={(e) => setForm((f) => ({ ...f, cpu_quota: e.target.value }))} />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Environment Variables <span className="text-muted-foreground">(KEY=value per line)</span></Label>
              <Textarea
                value={form.env_vars}
                onChange={(e) => setForm((f) => ({ ...f, env_vars: e.target.value }))}
                placeholder="API_URL=http://..."
                rows={3}
              />
            </div>

            <div className="space-y-1.5">
              <Label>Secret Refs <span className="text-muted-foreground">(comma-separated)</span></Label>
              <Input value={form.secret_refs} onChange={(e) => setForm((f) => ({ ...f, secret_refs: e.target.value }))} />
            </div>

            {/* Mounts */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Filesystem Mounts</Label>
                <Button type="button" variant="outline" size="sm" onClick={addMount}>
                  <Plus className="h-3 w-3" /> Add Mount
                </Button>
              </div>
              {form.mounts.map((m, i) => (
                <div key={i} className="flex gap-2 items-center rounded-md border border-border p-2">
                  <Input
                    placeholder="Host path"
                    value={m.host_path}
                    onChange={(e) => updateMount(i, 'host_path', e.target.value)}
                    className="flex-1"
                  />
                  <Input
                    placeholder="Container path"
                    value={m.container_path}
                    onChange={(e) => updateMount(i, 'container_path', e.target.value)}
                    className="flex-1"
                  />
                  <div className="flex items-center gap-1">
                    <Switch
                      checked={m.read_only}
                      onCheckedChange={(v) => updateMount(i, 'read_only', v)}
                    />
                    <span className="text-xs text-muted-foreground">RO</span>
                  </div>
                  <Button type="button" variant="ghost" size="icon" onClick={() => removeMount(i)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          </div>

          {error && <p className="text-sm text-red-400">{error}</p>}

          <DialogFooter>
            <Button variant="outline" onClick={closeDialog}>Cancel</Button>
            <Button onClick={handleSubmit} disabled={isPending || !form.name || !form.image}>
              {isPending ? 'Saving…' : editing ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
