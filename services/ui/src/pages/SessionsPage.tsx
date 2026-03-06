import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Plus, Play, StopCircle } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import { agentsApi } from '@/api/agents'
import { policiesApi } from '@/api/policies'
import { graphsApi } from '@/api/graphs'
import type { SessionCreate } from '@/types'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
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
import { truncate, formatDate } from '@/lib/utils'

const STATUS_VARIANT: Record<string, 'default' | 'success' | 'destructive' | 'warning' | 'secondary'> = {
  running: 'default',
  completed: 'success',
  failed: 'destructive',
  aborted: 'warning',
  pending: 'secondary',
}

export default function SessionsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<SessionCreate>({ agent_id: '', policy_id: '', execution_graph_id: null, initial_prompt: '' })
  const [error, setError] = useState('')

  const { data: sessions = [], isLoading } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => sessionsApi.list(),
    refetchInterval: 5000,
  })
  const { data: agents = [] } = useQuery({ queryKey: ['agents'], queryFn: agentsApi.list })
  const { data: policies = [] } = useQuery({ queryKey: ['policies'], queryFn: policiesApi.list })
  const { data: graphs = [] } = useQuery({ queryKey: ['graphs'], queryFn: graphsApi.list })

  const createMut = useMutation({
    mutationFn: sessionsApi.create,
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      setOpen(false)
      navigate(`/sessions/${s.id}`)
    },
    onError: (e: Error) => setError(e.message),
  })

  const abortMut = useMutation({
    mutationFn: sessionsApi.abort,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  })

  function handleSubmit() {
    setError('')
    createMut.mutate(form)
  }

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Sessions</h1>
          <p className="text-sm text-muted-foreground">Launch and monitor agent execution sessions</p>
        </div>
        <Button onClick={() => { setForm({ agent_id: '', policy_id: '', execution_graph_id: null, initial_prompt: '' }); setError(''); setOpen(true) }}>
          <Plus className="h-4 w-4" /> New Session
        </Button>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
          <Play className="h-12 w-12 opacity-30" />
          <p>No sessions yet. Start one to run an agent.</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Agent</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Tokens Used</TableHead>
              <TableHead>Iterations</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sessions.map((s) => {
              const agent = agents.find((a) => a.id === s.agent_id)
              return (
                <TableRow
                  key={s.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/sessions/${s.id}`)}
                >
                  <TableCell className="font-mono text-xs text-muted-foreground">{truncate(s.id, 8)}</TableCell>
                  <TableCell>{agent?.name ?? truncate(s.agent_id)}</TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[s.status] ?? 'secondary'}>{s.status}</Badge>
                  </TableCell>
                  <TableCell className="tabular-nums">{s.tokens_used.toLocaleString()}</TableCell>
                  <TableCell>{s.iterations_completed}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{formatDate(s.created_at)}</TableCell>
                  <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                    {s.status === 'running' && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-destructive hover:text-destructive"
                        onClick={() => abortMut.mutate(s.id)}
                      >
                        <StopCircle className="h-4 w-4" />
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New Session</DialogTitle>
          </DialogHeader>

          <div className="grid gap-4 py-2">
            <div className="space-y-1.5">
              <Label>Agent *</Label>
              <Select value={form.agent_id} onValueChange={(v) => setForm((f) => ({ ...f, agent_id: v }))}>
                <SelectTrigger><SelectValue placeholder="Select agent…" /></SelectTrigger>
                <SelectContent>
                  {agents.map((a) => <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Policy *</Label>
              <Select value={form.policy_id} onValueChange={(v) => setForm((f) => ({ ...f, policy_id: v }))}>
                <SelectTrigger><SelectValue placeholder="Select policy…" /></SelectTrigger>
                <SelectContent>
                  {policies.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Execution Graph <span className="text-muted-foreground">(optional)</span></Label>
              <Select
                value={form.execution_graph_id ?? '_none'}
                onValueChange={(v) => setForm((f) => ({ ...f, execution_graph_id: v === '_none' ? null : v }))}
              >
                <SelectTrigger><SelectValue placeholder="None (policy-only)" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="_none">None (policy-only enforcement)</SelectItem>
                  {graphs.map((g) => <SelectItem key={g.id} value={g.id}>{g.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Initial Prompt</Label>
              <Textarea
                value={form.initial_prompt}
                onChange={(e) => setForm((f) => ({ ...f, initial_prompt: e.target.value }))}
                placeholder="Enter the initial prompt for the agent…"
                rows={4}
              />
            </div>
          </div>

          {error && <p className="text-sm text-red-400">{error}</p>}

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
            <Button
              onClick={handleSubmit}
              disabled={createMut.isPending || !form.agent_id || !form.policy_id}
            >
              {createMut.isPending ? 'Starting…' : 'Start Session'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
