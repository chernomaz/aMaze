import { useState, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, addEdge,
  type Node, type Edge, type Connection,
  Handle, Position, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Plus, Pencil, Trash2, GitFork, Save, X } from 'lucide-react'
import { graphsApi } from '@/api/graphs'
import type { GraphResponse, GraphCreate, CallType, StepCreate } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent,
} from '@/components/ui/dialog'
import {
  Table, TableHeader, TableBody, TableRow, TableHead, TableCell,
} from '@/components/ui/table'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'

// ─── Node types ───────────────────────────────────────────────────────────────

type StepNodeData = {
  step_id: number
  label: string
  call_type: CallType
  callee_id?: string
  max_loops: number
  token_cap?: number
  onSelect: (id: string) => void
}

const CALL_TYPE_COLORS: Record<CallType, string> = {
  llm_call: 'border-blue-500 bg-blue-500/10',
  mcp_call: 'border-green-500 bg-green-500/10',
  agent_call: 'border-purple-500 bg-purple-500/10',
  terminal: 'border-orange-500 bg-orange-500/10',
}

const CALL_TYPE_LABEL: Record<CallType, string> = {
  llm_call: 'LLM',
  mcp_call: 'MCP',
  agent_call: 'Agent',
  terminal: 'Output',
}

function StepNode({ id, data }: NodeProps) {
  const d = data as StepNodeData
  return (
    <div
      className={`min-w-[140px] cursor-pointer rounded-lg border-2 p-3 text-sm ${CALL_TYPE_COLORS[d.call_type]} transition-shadow hover:shadow-lg`}
      onClick={() => d.onSelect(id)}
    >
      <Handle type="target" position={Position.Top} className="!bg-muted-foreground" />
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-foreground">{d.label || `Step ${d.step_id}`}</span>
        <Badge variant="secondary" className="text-xs px-1 py-0">{CALL_TYPE_LABEL[d.call_type]}</Badge>
      </div>
      {d.callee_id && <p className="mt-1 truncate text-xs text-muted-foreground">{d.callee_id}</p>}
      <p className="mt-0.5 text-xs text-muted-foreground">loops: {d.max_loops}{d.token_cap ? ` · cap: ${d.token_cap}` : ''}</p>
      <Handle type="source" position={Position.Bottom} className="!bg-muted-foreground" />
    </div>
  )
}

const nodeTypes = { step: StepNode }

// ─── Graph ↔ ReactFlow conversion ─────────────────────────────────────────────

function graphToFlow(graph: GraphResponse, onSelect: (id: string) => void): { nodes: Node[]; edges: Edge[] } {
  const COL_W = 200
  const ROW_H = 120

  // Simple topological layout: BFS from start
  const stepMap = new Map(graph.steps.map((s) => [s.step_id, s]))
  const levels = new Map<number, number>()
  const queue = [graph.start_step_id]
  levels.set(graph.start_step_id, 0)
  while (queue.length) {
    const cur = queue.shift()!
    const step = stepMap.get(cur)
    if (!step) continue
    for (const next of step.next_step_ids) {
      if (!levels.has(next)) {
        levels.set(next, (levels.get(cur) ?? 0) + 1)
        queue.push(next)
      }
    }
  }

  // Count items per level for horizontal positioning
  const levelCount = new Map<number, number>()
  const levelIdx = new Map<number, number>()
  for (const [sid, lvl] of levels) {
    levelIdx.set(sid, levelCount.get(lvl) ?? 0)
    levelCount.set(lvl, (levelCount.get(lvl) ?? 0) + 1)
  }

  const nodes: Node[] = graph.steps.map((s) => {
    const lvl = levels.get(s.step_id) ?? 0
    const idx = levelIdx.get(s.step_id) ?? 0
    const total = levelCount.get(lvl) ?? 1
    return {
      id: String(s.step_id),
      type: 'step',
      position: { x: (idx - (total - 1) / 2) * COL_W, y: lvl * ROW_H },
      data: {
        step_id: s.step_id,
        label: s.label,
        call_type: s.call_type,
        callee_id: s.callee_id ?? undefined,
        max_loops: s.max_loops,
        token_cap: s.token_cap ?? undefined,
        onSelect,
      } as StepNodeData,
    }
  })

  const edges: Edge[] = []
  for (const s of graph.steps) {
    for (const next of s.next_step_ids) {
      edges.push({
        id: `${s.step_id}-${next}`,
        source: String(s.step_id),
        target: String(next),
        animated: true,
      })
    }
  }

  return { nodes, edges }
}

function flowToPayload(
  name: string,
  description: string,
  on_violation: string,
  nodes: Node[],
  edges: Edge[],
): GraphCreate {
  if (nodes.length === 0) throw new Error('Graph must have at least one step')

  const steps: StepCreate[] = nodes.map((n) => {
    const d = n.data as StepNodeData
    const next_step_ids = edges
      .filter((e) => e.source === n.id)
      .map((e) => parseInt(e.target))
    return {
      step_id: d.step_id,
      label: d.label,
      call_type: d.call_type,
      callee_id: d.callee_id || null,
      next_step_ids,
      max_loops: d.max_loops,
      token_cap: d.token_cap ?? null,
    }
  })

  // start step = lowest step_id (or only node without incoming edges)
  const hasIncoming = new Set(edges.map((e) => e.target))
  const startCandidates = nodes.filter((n) => !hasIncoming.has(n.id))
  const startNode = startCandidates[0] ?? nodes[0]
  const start_step_id = (startNode.data as StepNodeData).step_id

  return { name, description, start_step_id, on_violation, steps }
}

// ─── Step config panel ────────────────────────────────────────────────────────

type StepConfig = {
  label: string
  call_type: CallType
  callee_id: string
  max_loops: string
  token_cap: string
}

function StepConfigPanel({
  nodeId,
  data,
  onUpdate,
  onClose,
  onDelete,
}: {
  nodeId: string
  data: StepNodeData
  onUpdate: (id: string, updates: Partial<StepNodeData>) => void
  onClose: () => void
  onDelete: (id: string) => void
}) {
  const [cfg, setCfg] = useState<StepConfig>({
    label: data.label,
    call_type: data.call_type,
    callee_id: data.callee_id ?? '',
    max_loops: String(data.max_loops),
    token_cap: data.token_cap != null ? String(data.token_cap) : '',
  })

  function apply() {
    onUpdate(nodeId, {
      label: cfg.label,
      call_type: cfg.call_type,
      callee_id: cfg.callee_id || undefined,
      max_loops: parseInt(cfg.max_loops) || 1,
      token_cap: cfg.token_cap ? parseInt(cfg.token_cap) : undefined,
    })
    onClose()
  }

  return (
    <div className="absolute right-3 top-3 z-10 w-64 rounded-lg border border-border bg-card p-4 shadow-xl">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-sm">Step {data.step_id}</h3>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="space-y-3">
        <div className="space-y-1">
          <Label className="text-xs">Label</Label>
          <Input value={cfg.label} onChange={(e) => setCfg((c) => ({ ...c, label: e.target.value }))} className="h-7 text-xs" />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Call Type</Label>
          <Select value={cfg.call_type} onValueChange={(v) => setCfg((c) => ({ ...c, call_type: v as CallType }))}>
            <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="llm_call">LLM Call</SelectItem>
              <SelectItem value="mcp_call">MCP Call</SelectItem>
              <SelectItem value="agent_call">Agent Call</SelectItem>
              <SelectItem value="terminal">Terminal</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Callee ID</Label>
          <Input value={cfg.callee_id} onChange={(e) => setCfg((c) => ({ ...c, callee_id: e.target.value }))} className="h-7 text-xs" placeholder="tool name / agent id" />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-xs">Max Loops</Label>
            <Input type="number" value={cfg.max_loops} onChange={(e) => setCfg((c) => ({ ...c, max_loops: e.target.value }))} className="h-7 text-xs" />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Token Cap</Label>
            <Input type="number" value={cfg.token_cap} onChange={(e) => setCfg((c) => ({ ...c, token_cap: e.target.value }))} className="h-7 text-xs" placeholder="∞" />
          </div>
        </div>
        <div className="flex gap-2 pt-1">
          <Button size="sm" className="flex-1 h-7 text-xs" onClick={apply}>Apply</Button>
          <Button size="sm" variant="destructive" className="h-7 text-xs" onClick={() => { onDelete(nodeId); onClose() }}>
            <Trash2 className="h-3 w-3" />
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── Editor modal ──────────────────────────────────────────────────────────────

function GraphEditor({
  initial,
  onSave,
  onClose,
  isSaving,
  error,
}: {
  initial?: GraphResponse
  onSave: (payload: GraphCreate) => void
  onClose: () => void
  isSaving: boolean
  error: string
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [onViolation, setOnViolation] = useState(initial?.on_violation ?? 'block')
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [nextStepId, setNextStepId] = useState(() => {
    if (!initial) return 1
    return Math.max(...initial.steps.map((s) => s.step_id)) + 1
  })

  const handleSelect = useCallback((id: string) => setSelectedNodeId(id), [])

  const initFlow = initial
    ? graphToFlow(initial, handleSelect)
    : { nodes: [], edges: [] }

  const [nodes, setNodes, onNodesChange] = useNodesState(initFlow.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initFlow.edges)

  const onConnect = useCallback(
    (connection: Connection) => setEdges((eds) => addEdge({ ...connection, animated: true }, eds)),
    [setEdges],
  )

  function addNode(call_type: CallType) {
    const id = String(nextStepId)
    setNextStepId((n) => n + 1)
    const newNode: Node = {
      id,
      type: 'step',
      position: { x: Math.random() * 300 + 50, y: Math.random() * 200 + 50 },
      data: {
        step_id: parseInt(id),
        label: CALL_TYPE_LABEL[call_type],
        call_type,
        max_loops: 1,
        onSelect: handleSelect,
      } as StepNodeData,
    }
    setNodes((ns) => [...ns, newNode])
  }

  function updateNodeData(nodeId: string, updates: Partial<StepNodeData>) {
    setNodes((ns) =>
      ns.map((n) =>
        n.id === nodeId
          ? { ...n, data: { ...n.data, ...updates, onSelect: handleSelect } }
          : n,
      ),
    )
  }

  function deleteNode(nodeId: string) {
    setNodes((ns) => ns.filter((n) => n.id !== nodeId))
    setEdges((es) => es.filter((e) => e.source !== nodeId && e.target !== nodeId))
  }

  function handleSave() {
    try {
      const payload = flowToPayload(name, description, onViolation, nodes, edges)
      onSave(payload)
    } catch (e) {
      // error is shown via parent
    }
  }

  const selectedNode = nodes.find((n) => n.id === selectedNodeId)

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Graph name *"
          className="w-48 h-8"
        />
        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description"
          className="flex-1 h-8"
        />
        <Select value={onViolation} onValueChange={setOnViolation}>
          <SelectTrigger className="w-32 h-8 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="block">Block on violation</SelectItem>
            <SelectItem value="warn">Warn on violation</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" className="h-8" onClick={onClose}>Cancel</Button>
        <Button size="sm" className="h-8" onClick={handleSave} disabled={isSaving || !name}>
          <Save className="h-3.5 w-3.5" />{isSaving ? 'Saving…' : 'Save'}
        </Button>
      </div>

      {error && <p className="px-4 py-1 text-xs text-red-400 bg-red-500/10">{error}</p>}

      {/* Toolbar */}
      <div className="flex gap-2 px-4 py-2 border-b border-border bg-card/50">
        <span className="text-xs text-muted-foreground self-center mr-1">Add step:</span>
        {(['llm_call', 'mcp_call', 'agent_call', 'terminal'] as CallType[]).map((t) => (
          <Button key={t} variant="outline" size="sm" className="h-7 text-xs" onClick={() => addNode(t)}>
            <Plus className="h-3 w-3" />{CALL_TYPE_LABEL[t]}
          </Button>
        ))}
      </div>

      {/* Canvas */}
      <div className="relative flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          fitView
          className="bg-background"
        >
          <Background color="#334155" gap={20} />
          <Controls />
          <MiniMap nodeColor="#334155" maskColor="rgba(0,0,0,0.3)" />
        </ReactFlow>

        {selectedNode && (
          <StepConfigPanel
            nodeId={selectedNode.id}
            data={selectedNode.data as StepNodeData}
            onUpdate={updateNodeData}
            onClose={() => setSelectedNodeId(null)}
            onDelete={deleteNode}
          />
        )}
      </div>
    </div>
  )
}

// ─── Main page ─────────────────────────────────────────────────────────────────

export default function GraphsPage() {
  const qc = useQueryClient()
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingGraph, setEditingGraph] = useState<GraphResponse | null>(null)
  const [editorError, setEditorError] = useState('')

  const { data: graphs = [], isLoading } = useQuery({ queryKey: ['graphs'], queryFn: graphsApi.list })

  const createMut = useMutation({
    mutationFn: graphsApi.create,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['graphs'] }); setEditorOpen(false) },
    onError: (e: Error) => setEditorError(e.message),
  })
  const updateMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: GraphCreate }) => graphsApi.update(id, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['graphs'] }); setEditorOpen(false) },
    onError: (e: Error) => setEditorError(e.message),
  })
  const deleteMut = useMutation({
    mutationFn: graphsApi.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['graphs'] }),
  })

  function openCreate() { setEditingGraph(null); setEditorError(''); setEditorOpen(true) }
  function openEdit(g: GraphResponse) { setEditingGraph(g); setEditorError(''); setEditorOpen(true) }

  function handleSave(payload: GraphCreate) {
    setEditorError('')
    if (editingGraph) updateMut.mutate({ id: editingGraph.id, body: payload })
    else createMut.mutate(payload)
  }

  const isSaving = createMut.isPending || updateMut.isPending

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Graphs</h1>
          <p className="text-sm text-muted-foreground">Design execution graphs to control agent flow</p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="h-4 w-4" /> New Graph
        </Button>
      </div>

      {isLoading ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : graphs.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-16 text-muted-foreground">
          <GitFork className="h-12 w-12 opacity-30" />
          <p>No graphs yet.</p>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Steps</TableHead>
              <TableHead>Start Step</TableHead>
              <TableHead>On Violation</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {graphs.map((g) => (
              <TableRow key={g.id}>
                <TableCell>
                  <div>
                    <p className="font-medium">{g.name}</p>
                    {g.description && <p className="text-xs text-muted-foreground">{g.description}</p>}
                  </div>
                </TableCell>
                <TableCell>{g.steps.length}</TableCell>
                <TableCell>
                  <Badge variant="secondary">#{g.start_step_id}</Badge>
                </TableCell>
                <TableCell>
                  <Badge variant={g.on_violation === 'block' ? 'destructive' : 'warning'}>{g.on_violation}</Badge>
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-2">
                    <Button variant="ghost" size="icon" onClick={() => openEdit(g)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-destructive hover:text-destructive"
                      onClick={() => deleteMut.mutate(g.id)}
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

      {/* Full-screen editor dialog */}
      <Dialog open={editorOpen} onOpenChange={setEditorOpen}>
        <DialogContent className="max-w-[95vw] w-[95vw] h-[90vh] p-0 flex flex-col overflow-hidden">
          <GraphEditor
            initial={editingGraph ?? undefined}
            onSave={handleSave}
            onClose={() => setEditorOpen(false)}
            isSaving={isSaving}
            error={editorError}
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}
