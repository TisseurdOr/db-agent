export interface SSEEvent {
  type: string;
  node?: string;
  task?: string;
  timestamp?: number;
  elapsed?: number;
  tokens?: number;
  text?: string;
  data?: InterruptData;
  total_elapsed?: number;
  trace_id?: string;
  opik_trace_id?: string;
  sql?: string;
  answer?: string;
  plan?: PlanStep[];
  charts?: ChartConfig[];
  stats?: Record<string, unknown>;
  message?: string;
  detail?: string;
  query_id?: string;
  session_id?: string;
}

export interface InterruptData {
  type: string;
  sql?: string;
  confidence?: number;
  message?: string;
  tool?: string;
  user?: string;
  role?: string;
}

export interface PlanStep {
  agent: string;
  task: string;
}

export interface ChartConfig {
  type: "bar" | "line" | "pie";
  title: string;
  labels: string[];
  values: number[];
}

export interface Step {
  node: string;
  task: string;
  status: "pending" | "running" | "done" | "error";
  elapsed?: number;
  tokens?: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming: boolean;
  steps: Step[];
  traceId: string;
  opikTraceId?: string;
  sql: string;
  charts: ChartConfig[];
  feedbackGiven: boolean;
  totalTokens?: number;
  totalElapsed?: number;
  cancelled?: boolean;
  hitl?: InterruptData;
}

export interface RbacUser {
  id: string;
  name: string;
  role: string;
  role_name: string;
  dept_id?: number | null;
  dept_name?: string | null;
}

export interface ChatState {
  messages: Message[];
  isStreaming: boolean;
  datasource: string;
  sessionId: string;
  userId: string;
  enableDq: boolean;
  hitlActive: boolean;
  hitlData: InterruptData | null;
}
