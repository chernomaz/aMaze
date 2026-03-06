import { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ReactFlow, Background, Controls,
  useNodesState, useEdgesState,
  type Node, type Edge,
  Handle, Position, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ArrowLeft, StopCircle, RefreshCw } from 'lucide-react'
import { sessionsApi } from '@/api/sessions'
import { graphsApi } from '@/api/graphs'
import { agentsApi } from '@/api/agents'
import { getWsUrl } from '@/api/client'
import type { AnyEvent, StepAdvancedEvent, StatusChangeEvent, GraphResponse, CallType } from '@/types'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { formatDate } from '@/lib/utils'

// ─── Read-only step node ───────────────────────────────────────────────────────

type StepNodeData = {
  step_id: number
  label: string
  call_type: CallType
  isActive: boolean
  loops: number
  tokens: number
}

const CALL_TYPE_COLORS: Record<string, string> = {
  llm_call: 'border-blue-500',
  mcp_call: 'border-green-500',
  agent_call: 'border-purple-500',
  terminal: 'border-orange-500',
}

function StepNode({ data }: NodeProps) {
  const d = data as StepNodeData
  return (
    <div
      className={`min-w-[130px] rounded-lg border-2 p-2.5 text-xs transition-all ${CALL_TYPE_COLORS[d.call_type] ?? 'border-border'} ${d.isActive ? 'shadow-lg shadow-primary/30 ring-2 ring-primary' : ''} bg-card`}
    >
      <Handle type="target" position={Position.Top} className="!bg-muted-foreground" />
      <p className="font-semibold text-foreground">{d.label || `Step ${d.step_id}`}</p>
      <p className="text-muted-foreground">{d.call_type}</p>
      {(d.loops > 0 || d.tokens > 0) && (
        <p className="mt-1 text-muted-foreground">
          {d.loops > 0 && `loops: ${d.loops}`}
          {d.loops > 0 && d.tokens > 0 && ' · '}
          {d.tokens > 0 && `tok: ${d.tokens}`}
        </p>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-muted-foreground" />
    </div>
  )
}

const nodeTypes = { step: StepNode }

// ─── Graph view ────────────────────────────────────────────────────────────────

function buildFlow(
  graph: GraphResponse,
  activeStep: number | null,
  stepLoops: Map<number, number>,
  stepTokens: Map<number, number>,
): { nodes: Node[]; edges: Edge[] } {
  const COL_W = 200
  const ROW_H = 120
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
        isActive: s.step_id === activeStep,
        loops: stepLoops.get(s.step_id) ?? 0,
        tokens: stepTokens.get(s.step_id) ?? 0,
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
        animated: s.step_id === activeStep,
      })
    }
  }
  return { nodes, edges }
}

// ─── Event log entry ───────────────────────────────────────────────────────────

function EventIcon({ type }: { type: string }) {
  const icons: Record<string, string> = {
    llm_call: '🤖',
    mcp_call: '🔧',
    agent_call: '🤝',
    policy_violation: '🚫',
    graph_violation: '⚠️',
    step_advanced: '➡️',
    status_change: '🔄',
    output: '📤',
    edge_loop_exceeded: '🔁',
    edge_token_cap_exceeded: '💰',
  }
  return <span>{icons[type] ?? '•'}</span>
}

function eventSummary(ev: AnyEvent): string {
  switch (ev.event_type) {
    case 'llm_call': {
      const e = ev as import('@/types').LLMCallEvent
      return `LLM call to ${e.provider}/${e.model} — ${e.total_tokens} tokens`
    }
    case 'mcp_call': {
      const e = ev as import('@/types').MCPCallEvent
      return `MCP ${e.tool_name} via ${e.mcp_server} — ${e.success ? 'ok' : 'failed'}`
    }
    case 'agent_call': {
      const e = ev as import('@/types').AgentCallEvent
      return `Agent call to ${e.target_agent}`
    }
    case 'policy_violation': {
      const e = ev as import('@/types').PolicyViolationEvent
      return `Policy violation: ${e.reason}`
    }
    case 'graph_violation': {
      const e = ev as import('@/types').GraphViolationEvent
      return `Graph violation: expected ${e.expected_call_type}, got ${e.got_call_type}`
    }
    case 'step_advanced': {
      const e = ev as StepAdvancedEvent
      return `Step advanced: ${e.from_step_id} → ${e.to_step_id}`
    }
    case 'status_change': {
      const e = ev as StatusChangeEvent
      return `Status: ${e.old_status} → ${e.new_status}`
    }
    case 'output': {
      const e = ev as import('@/types').OutputEvent
      return `Output: ${e.output.slice(0, 80)}${e.output.length > 80 ? '…' : ''}`
    }
    default:
      return ev.event_type
  }
}

// ─── Main page ─────────────────────────────────────────────────────────────────

const STATUS_VARIANT: Record<string, 'default' | 'success' | 'destructive' | 'warning' | 'secondary'> = {
  running: 'default',
  completed: 'success',
  failed: 'destructive',
  aborted: 'warning',
  pending: 'secondary',
}

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [liveEvents, setLiveEvents] = useState<AnyEvent[]>([])
  const [activeStep, setActiveStep] = useState<number | null>(null)
  const [stepLoops, setStepLoops] = useState<Map<number, number>>(new Map())
  const [stepTokens, setStepTokens] = useState<Map<number, number>>(new Map())
  const [wsStatus, setWsStatus] = useState<'connecting' | 'connected' | 'closed'>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  const { data: session, refetch: refetchSession } = useQuery({
    queryKey: ['session', id],
    queryFn: () => sessionsApi.get(id!),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 3000 : false),
  })

  const { data: historicEvents = [] } = useQuery({
    queryKey: ['session-events', id],
    queryFn: () => sessionsApi.events(id!),
    enabled: !!id,
  })

  const { data: graph } = useQuery({
    queryKey: ['graph', session?.execution_graph_id],
    queryFn: () => graphsApi.get(session!.execution_graph_id!),
    enabled: !!session?.execution_graph_id,
  })

  const { data: agent } = useQuery({
    queryKey: ['agent', session?.agent_id],
    queryFn: () => agentsApi.get(session!.agent_id),
    enabled: !!session?.agent_id,
  })

  const abortMut = useMutation({
    mutationFn: () => sessionsApi.abort(id!),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['session', id] }); refetchSession() },
  })

  // WebSocket
  useEffect(() => {
    if (!id) return
    const ws = new WebSocket(getWsUrl(id))
    wsRef.current = ws
    setWsStatus('connecting')

    ws.onopen = () => setWsStatus('connected')
    ws.onclose = () => setWsStatus('closed')

    ws.onmessage = (msg) => {
      try {
        const ev: AnyEvent = JSON.parse(msg.data)

        if (ev.event_type === 'connected') return

        setLiveEvents((prev) => [...prev, ev])

        if (ev.event_type === 'step_advanced') {
          const e = ev as StepAdvancedEvent
          setActiveStep(e.to_step_id)
          setStepLoops((m) => new Map(m).set(e.to_step_id, e.loops_on_step))
          setStepTokens((m) => new Map(m).set(e.to_step_id, e.tokens_on_step))
        }

        if (ev.event_type === 'status_change') {
          refetchSession()
        }
      } catch {
        // ignore parse errors
      }
    }

    return () => { ws.close() }
  }, [id, refetchSession])

  // Auto-scroll event log
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [liveEvents])

  // Build flow nodes/edges
  const { nodes: rfNodes, edges: rfEdges } = graph
    ? buildFlow(graph, activeStep, stepLoops, stepTokens)
    : { nodes: [], edges: [] }

  const [nodes, , onNodesChange] = useNodesState(rfNodes)
  const [edges, , onEdgesChange] = useEdgesState(rfEdges)

  // Update nodes when active step changes
  const updatedNodes = nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      isActive: (n.data as StepNodeData).step_id === activeStep,
      loops: stepLoops.get((n.data as StepNodeData).step_id) ?? 0,
      tokens: stepTokens.get((n.data as StepNodeData).step_id) ?? 0,
    },
  }))

  const allEvents = [...historicEvents.map((e) => ({ ...e.payload, event_type: e.event_type, timestamp: e.timestamp, step_id: e.step_id })), ...liveEvents] as AnyEvent[]

  const tokenBudget = session?.tokens_used ?? 0

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-border px-6 py-4">
        <Button variant="ghost" size="icon" onClick={() => navigate('/sessions')}>
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <h1 className="font-bold">Session</h1>
            <span className="font-mono text-xs text-muted-foreground">{id?.slice(0, 8)}</span>
            {session && <Badge variant={STATUS_VARIANT[session.status] ?? 'secondary'}>{session.status}</Badge>}
            <Badge variant={wsStatus === 'connected' ? 'success' : 'secondary'} className="text-xs">
              {wsStatus === 'connected' ? 'live' : wsStatus}
            </Badge>
          </div>
          {agent && <p className="text-xs text-muted-foreground">Agent: {agent.name}</p>}
        </div>
        {session && (
          <div className="flex items-center gap-4 text-sm">
            <div className="text-right">
              <p className="text-xs text-muted-foreground">Tokens used</p>
              <p className="font-mono font-semibold">{tokenBudget.toLocaleString()}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-muted-foreground">Iterations</p>
              <p className="font-mono font-semibold">{session.iterations_completed}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-muted-foreground">Started</p>
              <p className="text-xs">{formatDate(session.created_at)}</p>
            </div>
            {session.status === 'running' && (
              <Button variant="destructive" size="sm" onClick={() => abortMut.mutate()}>
                <StopCircle className="h-4 w-4" /> Abort
              </Button>
            )}
            <Button variant="ghost" size="icon" onClick={() => refetchSession()}>
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        )}
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Graph view */}
        <div className="flex-1 border-r border-border">
          {graph ? (
            <ReactFlow
              nodes={updatedNodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={nodeTypes}
              fitView
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              className="bg-background"
            >
              <Background color="#334155" gap={20} />
              <Controls showInteractive={false} />
            </ReactFlow>
          ) : (
            <div className="flex h-full items-center justify-center text-muted-foreground">
              <p className="text-sm">{session?.execution_graph_id ? 'Loading graph…' : 'No execution graph attached'}</p>
            </div>
          )}
        </div>

        {/* Event log */}
        <div className="flex w-96 flex-col">
          <div className="border-b border-border px-4 py-2">
            <h2 className="text-sm font-semibold">Event Log</h2>
            <p className="text-xs text-muted-foreground">{allEvents.length} events</p>
          </div>
          <div ref={logRef} className="flex-1 overflow-y-auto p-2 space-y-1">
            {allEvents.length === 0 ? (
              <p className="text-center text-xs text-muted-foreground py-8">Waiting for events…</p>
            ) : (
              allEvents.map((ev, i) => (
                <div
                  key={i}
                  className={`rounded px-2 py-1.5 text-xs ${
                    ev.event_type.includes('violation') || ev.event_type.includes('exceeded')
                      ? 'bg-red-500/10 text-red-400'
                      : ev.event_type === 'output'
                      ? 'bg-green-500/10 text-green-400'
                      : 'bg-muted/40 text-foreground'
                  }`}
                >
                  <div className="flex items-start gap-1.5">
                    <EventIcon type={ev.event_type} />
                    <span className="flex-1">{eventSummary(ev)}</span>
                  </div>
                  <p className="mt-0.5 text-muted-foreground opacity-70">{new Date(ev.timestamp).toLocaleTimeString()}</p>
                </div>
              ))
            )}
          </div>

          {/* Final output */}
          {session?.final_output && (
            <div className="border-t border-border p-3">
              <p className="mb-1 text-xs font-semibold text-green-400">Final Output</p>
              <p className="text-xs text-foreground max-h-32 overflow-y-auto">{session.final_output}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
