import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2, Shield } from 'lucide-react'
import { policiesApi } from '@/api/policies'
import type { PolicyResponse, PolicyCreate, PolicyUpdate } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog'
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

type FormState = {
  name: string
  description: string
  max_tokens_per_conversation: string
  max_tokens_per_turn: string
  max_iterations: string
  max_agent_calls: string
  max_mcp_calls: string
  allowed_llm_providers: string
  allowed_mcp_servers: string
  on_budget_exceeded: string
  on_loop_exceeded: string
}

const defaultForm = (): FormState => ({
  name: '',
  description: '',
  max_tokens_per_conversation: '100000',
  max_tokens_per_turn: '10000',
  max_iterations: '20',
  max_agent_calls: '10',
  max_mcp_calls: '50',
  allowed_llm_providers: '',
  allowed_mcp_servers: '',
  on_budget_exceeded: 'block',
  on_loop_exceeded: 'block',
})

function policyToForm(p: PolicyResponse): FormState {
  return {
    name: p.name,
    description: p.description,
    max_tokens_per_conversation: String(p.max_tokens_per_conversation),
    max_tokens_per_turn: String(p.max_tokens_per_turn),
    max_iterations: String(p.max_iterations),
    max_agent_calls: String(p.max_agent_calls),
    max_mcp_calls: String(p.max_mcp_calls),
    allowed_llm_providers: p.allowed_llm_providers.join(', '),
    allowed_mcp_servers: p.allowed_mcp_servers.join(', '),
    on_budget_exceeded: p.on_budget_exceeded,
    on_loop_exceeded: p.on_loop_exceeded,
  }
}

function formToPayload(f: FormState): PolicyCreate {
  return {
    name: f.name,
    description: f.description,
    max_tokens_per_conversation: parseInt(f.max_tokens_per_conversation),
    max_tokens_per_turn: parseInt(f.max_tokens_per_turn),
    max_iterations: parseInt(f.max_iterations),
    max_agent_calls: parseInt(f.max_agent_calls),
    max_mcp_calls: parseInt(f.max_mcp_calls),
    allowed_llm_providers: f.allowed_llm_providers.split(',').map((s) => s.trim()).filter(Boolean),
    allowed_mcp_servers: f.allowed_mcp_servers.split(',').map((s) => s.trim()).filter(Boolean),
    on_budget_exceeded: f.on_budget_exceeded,
    on_loop_exceeded: f.on_loop_exceeded,
  }
}

const VIOLATION_OPTIONS = [
  { value: 'block', label: 'Block' },
  { value: 'warn', label: 'Warn' },
  { value: 'allow', label: 'Allow' },
]

export default function PoliciesPage() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<PolicyResponse | null>(null)
  const [form, setForm] = useState<FormState>(defaultForm())
  const [error, setError] = useState('')

  const { data: policies = [], isLoading } = useQuery({ queryKey: ['policies'], queryFn: policiesApi.list })

  const createMut = useMutation({
    mutationFn: policiesApi.create,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['policies'] }); closeDialog() },
    onError: (e: Error) => setError(e.message),
  })
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: PolicyUpdate }) => policiesApi.update(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['policies'] }); closeDialog() },
    onError: (e: Error) => setError(e.message),
  })
  const deleteMut = useMutation({
    mutationFn: policiesApi.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['policies'] }),
  })

  function openCreate() { setEditing(null); setForm(defaultForm()); setError(''); setOpen(true) }
  function openEdit(p: PolicyResponse) { setEditing(p); setForm(policyToForm(p)); setError(''); setOpen(true) }
  function closeDialog() { setOpen(false); setEditing(null) }

  function handleSubmit() {
    setError('')
    const payload = formToPayload(form)
    if (editing) updateMut.mutate({ id: editing.id, body: payload })
    else createMut.mutate(payload)
  }

  const set = (key: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  const isPending = createMut.isPending || updateMut.isPending

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Policies</h1>
          <p className="text-sm text-muted-foreground">Define token budgets, loop limits, and tool allowlists</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> New Policy
        </Button>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : policies.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
          <Shield className="h-12 w-12 opacity-30" />
          <p>No policies yet.</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Token Budget</TableHead>
              <TableHead>Max Iterations</TableHead>
              <TableHead>MCP Calls</TableHead>
              <TableHead>On Exceed</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {policies.map((p) => (
              <TableRow key={p.id}>
                <TableCell>
                  <div>
                    <p className="font-medium">{p.name}</p>
                    {p.description && <p className="text-xs text-muted-foreground">{p.description}</p>}
                  </div>
                </TableCell>
                <TableCell className="tabular-nums">{p.max_tokens_per_conversation.toLocaleString()}</TableCell>
                <TableCell>{p.max_iterations}</TableCell>
                <TableCell>{p.max_mcp_calls}</TableCell>
                <TableCell>
                  <Badge variant={p.on_budget_exceeded === 'block' ? 'destructive' : 'warning'}>
                    {p.on_budget_exceeded}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button variant="ghost" size="icon" onClick={() => openEdit(p)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      onClick={() => deleteMut.mutate(p.id)}
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
        <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editing ? 'Edit Policy' : 'New Policy'}</DialogTitle>
          </DialogHeader>

          <div className="grid gap-4 py-2">
            <div className="space-y-1.5">
              <Label>Name *</Label>
              <Input value={form.name} onChange={set('name')} placeholder="default-policy" />
            </div>
            <div className="space-y-1.5">
              <Label>Description</Label>
              <Input value={form.description} onChange={set('description')} />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>Max Tokens / Conversation</Label>
                <Input type="number" value={form.max_tokens_per_conversation} onChange={set('max_tokens_per_conversation')} />
              </div>
              <div className="space-y-1.5">
                <Label>Max Tokens / Turn</Label>
                <Input type="number" value={form.max_tokens_per_turn} onChange={set('max_tokens_per_turn')} />
              </div>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1.5">
                <Label>Max Iterations</Label>
                <Input type="number" value={form.max_iterations} onChange={set('max_iterations')} />
              </div>
              <div className="space-y-1.5">
                <Label>Max Agent Calls</Label>
                <Input type="number" value={form.max_agent_calls} onChange={set('max_agent_calls')} />
              </div>
              <div className="space-y-1.5">
                <Label>Max MCP Calls</Label>
                <Input type="number" value={form.max_mcp_calls} onChange={set('max_mcp_calls')} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label>On Budget Exceeded</Label>
                <Select value={form.on_budget_exceeded} onValueChange={(v) => setForm((f) => ({ ...f, on_budget_exceeded: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {VIOLATION_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>On Loop Exceeded</Label>
                <Select value={form.on_loop_exceeded} onValueChange={(v) => setForm((f) => ({ ...f, on_loop_exceeded: v }))}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {VIOLATION_OPTIONS.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Allowed LLM Providers <span className="text-muted-foreground">(comma-separated, empty = all)</span></Label>
              <Input value={form.allowed_llm_providers} onChange={set('allowed_llm_providers')} placeholder="openai, anthropic" />
            </div>

            <div className="space-y-1.5">
              <Label>Allowed MCP Servers <span className="text-muted-foreground">(comma-separated, empty = all)</span></Label>
              <Input value={form.allowed_mcp_servers} onChange={set('allowed_mcp_servers')} placeholder="filesystem, websearch" />
            </div>
          </div>

          {error && <p className="text-sm text-red-400">{error}</p>}

          <DialogFooter>
            <Button variant="outline" onClick={closeDialog}>Cancel</Button>
            <Button onClick={handleSubmit} disabled={isPending || !form.name}>
              {isPending ? 'Saving…' : editing ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
