// ─── Agents ───────────────────────────────────────────────────────────────────

export interface MountResponse {
  id: string
  host_path: string
  container_path: string
  read_only: boolean
}

export interface AgentResponse {
  id: string
  name: string
  description: string
  image: string
  version: string
  capabilities: string[]
  required_capabilities: string[]
  env_vars: Record<string, string>
  secret_refs: string[]
  status: string
  policy_id: string | null
  mem_limit: string
  cpu_quota: number
  mounts: MountResponse[]
}

export interface MountInput {
  host_path: string
  container_path: string
  read_only: boolean
}

export interface AgentCreate {
  name: string
  description?: string
  image: string
  version?: string
  capabilities?: string[]
  required_capabilities?: string[]
  env_vars?: Record<string, string>
  secret_refs?: string[]
  policy_id?: string | null
  mem_limit?: string
  cpu_quota?: number
  mounts?: MountInput[]
}

export interface AgentUpdate extends Partial<AgentCreate> {}

// ─── Policies ─────────────────────────────────────────────────────────────────

export interface ToolPermission {
  tool_name: string
  allowed: boolean
  params_allowlist?: Record<string, unknown> | null
}

export interface PolicyResponse {
  id: string
  name: string
  description: string
  max_tokens_per_conversation: number
  max_tokens_per_turn: number
  max_iterations: number
  max_agent_calls: number
  max_mcp_calls: number
  allowed_tools: ToolPermission[]
  allowed_llm_providers: string[]
  allowed_mcp_servers: string[]
  on_budget_exceeded: string
  on_loop_exceeded: string
}

export interface PolicyCreate {
  name: string
  description?: string
  max_tokens_per_conversation?: number
  max_tokens_per_turn?: number
  max_iterations?: number
  max_agent_calls?: number
  max_mcp_calls?: number
  allowed_tools?: ToolPermission[]
  allowed_llm_providers?: string[]
  allowed_mcp_servers?: string[]
  on_budget_exceeded?: string
  on_loop_exceeded?: string
}

export interface PolicyUpdate extends Partial<PolicyCreate> {}

// ─── Graphs ───────────────────────────────────────────────────────────────────

export type CallType = 'llm_call' | 'mcp_call' | 'agent_call' | 'terminal'

export interface StepResponse {
  id: string
  step_id: number
  label: string
  call_type: CallType
  callee_id: string | null
  next_step_ids: number[]
  max_loops: number
  token_cap: number | null
}

export interface GraphResponse {
  id: string
  name: string
  description: string
  start_step_id: number
  on_violation: string
  steps: StepResponse[]
}

export interface StepCreate {
  step_id: number
  label?: string
  call_type: CallType
  callee_id?: string | null
  next_step_ids?: number[]
  max_loops?: number
  token_cap?: number | null
}

export interface GraphCreate {
  name: string
  description?: string
  start_step_id: number
  on_violation?: string
  steps: StepCreate[]
}

export interface GraphUpdate extends Partial<GraphCreate> {}

// ─── Sessions ─────────────────────────────────────────────────────────────────

export interface SessionResponse {
  id: string
  agent_id: string
  policy_id: string
  execution_graph_id: string | null
  container_id: string | null
  status: string
  initial_prompt: string
  final_output: string | null
  tokens_used: number
  iterations_completed: number
  mcp_calls_made: number
  agent_calls_made: number
  created_at: string
}

export interface SessionEventResponse {
  id: string
  event_type: string
  payload: Record<string, unknown>
  tokens_delta: number
  step_id: number | null
  timestamp: string
}

export interface SessionCreate {
  agent_id: string
  policy_id: string
  execution_graph_id?: string | null
  initial_prompt?: string
}

// ─── Registry ─────────────────────────────────────────────────────────────────

export interface RegistryEntry {
  id: string
  name: string
  capability_type: string
  version: string
  description: string
  internal_host: string
  internal_port: number
  input_schema: Record<string, unknown> | null
  output_schema: Record<string, unknown> | null
  tags: string[]
  is_healthy: boolean
  last_heartbeat: string
  registered_at: string
  owner_agent_id: string | null
}

// ─── WebSocket Events ─────────────────────────────────────────────────────────

export interface BaseEvent {
  session_id: string
  timestamp: string
  step_id: number | null
  event_type: string
}

export interface LLMCallEvent extends BaseEvent {
  event_type: 'llm_call'
  provider: string
  model: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface MCPCallEvent extends BaseEvent {
  event_type: 'mcp_call'
  tool_name: string
  mcp_server: string
  success: boolean
}

export interface AgentCallEvent extends BaseEvent {
  event_type: 'agent_call'
  target_agent: string
  child_session_id: string | null
}

export interface PolicyViolationEvent extends BaseEvent {
  event_type: 'policy_violation'
  violation_type: string
  limit: number | null
  current: number | null
  reason: string
}

export interface GraphViolationEvent extends BaseEvent {
  event_type: 'graph_violation'
  expected_call_type: string
  expected_callee_id: string | null
  got_call_type: string
  got_callee_id: string | null
}

export interface StepAdvancedEvent extends BaseEvent {
  event_type: 'step_advanced'
  from_step_id: number
  to_step_id: number
  loops_on_step: number
  tokens_on_step: number
}

export interface StatusChangeEvent extends BaseEvent {
  event_type: 'status_change'
  old_status: string
  new_status: string
}

export interface OutputEvent extends BaseEvent {
  event_type: 'output'
  output: string
}

export type AnyEvent =
  | LLMCallEvent
  | MCPCallEvent
  | AgentCallEvent
  | PolicyViolationEvent
  | GraphViolationEvent
  | StepAdvancedEvent
  | StatusChangeEvent
  | OutputEvent
  | BaseEvent
