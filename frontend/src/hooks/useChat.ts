import { useReducer, useCallback } from "react";
import { loadPersistedUserId, persistUserId } from "../rbacStorage";
import type { ChatState, Message, SSEEvent, InterruptData, ChartConfig, StoredMessage } from "../types";

type Action =
  | { type: "ADD_USER_MESSAGE"; content: string }
  | { type: "START_ASSISTANT_MESSAGE" }
  | { type: "STEP_START"; node: string; task: string }
  | { type: "STEP_END"; node: string; task: string; elapsed: number; tokens: number }
  | { type: "TEXT_DELTA"; text: string }
  | { type: "SET_STREAMING"; isStreaming: boolean }
  | { type: "DONE"; traceId: string; opikTraceId?: string; sql: string; answer: string; charts: ChartConfig[]; plan: { agent: string; task: string }[]; tokens?: number; totalElapsed?: number }
  | { type: "ERROR"; message: string }
  | { type: "INTERRUPT"; data: InterruptData }
  | { type: "RESUME" }
  | { type: "CANCEL_LAST_STREAM" }
  | { type: "FEEDBACK"; messageId: string; rating: "up" | "down" }
  | { type: "SET_DATASOURCE"; datasource: string }
  | { type: "SET_USER_ID"; userId: string }
  | { type: "SET_ENABLE_DQ"; enableDq: boolean }
  | { type: "NEW_CHAT"; sessionId: string }
  | { type: "LOAD_SESSION"; sessionId: string; messages: Message[] };

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function findLastAssistantIndex(messages: Message[]): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") return i;
  }
  return -1;
}

/** Immutable update of the latest assistant message (required under React StrictMode). */
function updateLastAssistant(
  state: ChatState,
  updater: (msg: Message) => Message,
  extra: Partial<ChatState> = {},
): ChatState {
  const idx = findLastAssistantIndex(state.messages);
  if (idx < 0) return { ...state, ...extra };
  const messages = state.messages.map((m, i) => (i === idx ? updater(m) : m));
  return { ...state, messages, ...extra };
}

function chatReducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case "ADD_USER_MESSAGE": {
      const userMsg: Message = {
        id: newId(),
        role: "user",
        content: action.content,
        isStreaming: false,
        steps: [],
        traceId: "",
        sql: "",
        charts: [],
        feedbackGiven: false,
        totalTokens: 0,
        totalElapsed: 0,
      };
      return { ...state, messages: [...state.messages, userMsg] };
    }

    case "START_ASSISTANT_MESSAGE": {
      const assistantMsg: Message = {
        id: newId(),
        role: "assistant",
        content: "",
        isStreaming: true,
        steps: [],
        traceId: "",
        sql: "",
        charts: [],
        feedbackGiven: false,
        totalTokens: 0,
        totalElapsed: 0,
        cancelled: false,
      };
      return {
        ...state,
        messages: [...state.messages, assistantMsg],
        isStreaming: true,
      };
    }

    case "STEP_START": {
      return updateLastAssistant(state, (last) => ({
        ...last,
        steps: [
          ...last.steps,
          { node: action.node, task: action.task, status: "running" as const },
        ],
      }));
    }

    case "STEP_END": {
      return updateLastAssistant(state, (last) => {
        let added = 0;
        const steps = last.steps.map((s) => {
          if (s.node === action.node && s.status === "running") {
            added = action.tokens || 0;
            return {
              ...s,
              status: "done" as const,
              elapsed: action.elapsed,
              tokens: action.tokens,
            };
          }
          return s;
        });
        return {
          ...last,
          steps,
          totalTokens: (last.totalTokens || 0) + added,
        };
      });
    }

    case "TEXT_DELTA": {
      return updateLastAssistant(state, (last) => ({
        ...last,
        content: last.content + action.text,
      }));
    }

    case "SET_STREAMING": {
      return { ...state, isStreaming: action.isStreaming };
    }

    case "DONE": {
      return updateLastAssistant(
        state,
        (last) => {
          const steps = [...last.steps];
          for (const p of action.plan || []) {
            if (!steps.find((s) => s.node === p.agent)) {
              steps.push({
                node: p.agent,
                task: p.task,
                status: "done",
              });
            }
          }
          const fromSteps = steps.reduce((n, s) => n + (s.tokens || 0), 0);
          const totalTokens = Math.max(action.tokens || 0, fromSteps, last.totalTokens || 0);
          return {
            ...last,
            isStreaming: false,
            traceId: action.traceId,
            opikTraceId: action.opikTraceId || "",
            sql: action.sql,
            charts: action.charts,
            content: action.answer && !last.content ? action.answer : last.content,
            steps,
            totalTokens,
            totalElapsed: action.totalElapsed ?? last.totalElapsed,
          };
        },
        { isStreaming: false, hitlActive: false },
      );
    }

    case "ERROR": {
      return updateLastAssistant(
        state,
        (last) => ({
          ...last,
          content: `❌ ${action.message}`,
          isStreaming: false,
        }),
        { isStreaming: false, hitlActive: false },
      );
    }

    case "INTERRUPT": {
      return updateLastAssistant(
        state,
        (last) => ({ ...last, hitl: action.data }),
        { isStreaming: false, hitlActive: true, hitlData: action.data },
      );
    }

    case "RESUME": {
      return updateLastAssistant(
        state,
        (last) => ({ ...last, hitl: undefined, isStreaming: true }),
        { isStreaming: true, hitlActive: false, hitlData: null },
      );
    }

    case "CANCEL_LAST_STREAM": {
      return updateLastAssistant(
        state,
        (last) => {
          if (!last.isStreaming) return last;
          return {
            ...last,
            isStreaming: false,
            cancelled: true,
            content: last.content || "⚠️ 已取消（被新查询打断，后端请求已中止）",
            steps: last.steps.map((s) =>
              s.status === "running" ? { ...s, status: "error" as const } : s,
            ),
          };
        },
        { isStreaming: false, hitlActive: false, hitlData: null },
      );
    }

    case "FEEDBACK": {
      const messages = state.messages.map((m) =>
        m.id === action.messageId
          ? { ...m, feedbackGiven: true }
          : m,
      );
      return { ...state, messages };
    }

    case "SET_DATASOURCE": {
      return { ...state, datasource: action.datasource };
    }

    case "SET_USER_ID": {
      persistUserId(action.userId);
      return { ...state, userId: action.userId };
    }

    case "SET_ENABLE_DQ": {
      return { ...state, enableDq: action.enableDq };
    }

    case "NEW_CHAT": {
      return {
        ...state,
        messages: [],
        isStreaming: false,
        sessionId: action.sessionId,
        hitlActive: false,
        hitlData: null,
      };
    }

    case "LOAD_SESSION": {
      // 切到历史会话：整段替换消息，并停掉任何进行中的流式状态
      return {
        ...state,
        messages: action.messages,
        isStreaming: false,
        sessionId: action.sessionId,
        hitlActive: false,
        hitlData: null,
      };
    }

    default:
      return state;
  }
}

function newSessionId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID().slice(0, 8);
  }
  return `s-${Date.now().toString(36)}`;
}


function createInitialState(): ChatState {
  return {
    messages: [],
    isStreaming: false,
    datasource: "sqlite",
    sessionId: newSessionId(),
    userId: loadPersistedUserId(),
    enableDq: false,
    hitlActive: false,
    hitlData: null,
  };
}

/** 后端存的历史消息 → 前端 Message。
 *
 * 服务端存了 sql / charts / plan / tokens / elapsed，但**没存逐步的 step 事件**，
 * 所以这里用 plan 重建一个"执行计划"步骤列表 —— 点回旧对话时仍能看到它当时派了哪些 Agent。
 */
export function storedToMessages(stored: StoredMessage[]): Message[] {
  return stored.map((m, i) => ({
    id: `hist-${i}`,
    role: m.role,
    content: m.content || "",
    isStreaming: false,
    steps: (m.plan || []).map((p) => ({
      node: p.agent,
      task: p.task,
      status: "done" as const,
    })),
    traceId: m.trace_id || "",
    sql: m.sql || "",
    charts: m.charts || [],
    feedbackGiven: false,
    totalTokens: m.tokens || 0,
    totalElapsed: m.elapsed || 0,
  }));
}

export function useChat() {
  // lazy init：每次挂载都读 localStorage（避免 HMR/模块缓存钉死 viewer）
  const [state, dispatch] = useReducer(chatReducer, undefined, createInitialState);

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    const raw = event as unknown as Record<string, unknown>;
    const etype = (raw._eventType as string) || event.type;
    switch (etype) {
      case "connected":
        // 结束乐观 connecting 步骤
        dispatch({
          type: "STEP_END",
          node: "connecting",
          task: "已连接",
          elapsed: 0,
          tokens: 0,
        });
        break;
      case "step_start":
        dispatch({
          type: "STEP_START",
          node: event.node || "",
          task: event.task || "",
        });
        break;
      case "step_end":
        dispatch({
          type: "STEP_END",
          node: event.node || "",
          task: event.task || "",
          elapsed: event.elapsed || 0,
          tokens: event.tokens || 0,
        });
        break;
      case "text_delta":
        dispatch({ type: "TEXT_DELTA", text: event.text || "" });
        break;
      case "interrupt":
        dispatch({ type: "INTERRUPT", data: event.data || (event as unknown as InterruptData) });
        break;
      case "done":
        dispatch({
          type: "DONE",
          traceId: event.trace_id || "",
          opikTraceId: event.opik_trace_id || "",
          sql: event.sql || "",
          answer: event.answer || "",
          charts: event.charts || [],
          plan: event.plan || [],
          tokens: event.tokens || (event.stats as { tokens?: number } | undefined)?.tokens || 0,
          totalElapsed: event.total_elapsed || 0,
        });
        break;
      case "error":
        dispatch({ type: "ERROR", message: event.message || "未知错误" });
        break;
    }
  }, []);

  const addUserMessage = useCallback((content: string) => {
    dispatch({ type: "ADD_USER_MESSAGE", content });
  }, []);

  const startAssistant = useCallback(() => {
    dispatch({ type: "START_ASSISTANT_MESSAGE" });
  }, []);

  const cancelLastStream = useCallback(() => {
    dispatch({ type: "CANCEL_LAST_STREAM" });
  }, []);

  const setStreaming = useCallback((v: boolean) => {
    dispatch({ type: "SET_STREAMING", isStreaming: v });
  }, []);

  const giveFeedback = useCallback((messageId: string, rating: "up" | "down") => {
    dispatch({ type: "FEEDBACK", messageId, rating });
  }, []);

  const resume = useCallback(() => {
    dispatch({ type: "RESUME" });
  }, []);

  const setDatasource = useCallback((ds: string) => {
    dispatch({ type: "SET_DATASOURCE", datasource: ds });
  }, []);

  const setUserId = useCallback((userId: string) => {
    persistUserId(userId);
    dispatch({ type: "SET_USER_ID", userId });
  }, []);

  const setEnableDq = useCallback((enableDq: boolean) => {
    dispatch({ type: "SET_ENABLE_DQ", enableDq });
  }, []);

  const newChat = useCallback(() => {
    dispatch({ type: "NEW_CHAT", sessionId: newSessionId() });
  }, []);

  /** 切到历史会话：把后端存的消息灌回状态机。 */
  const loadSession = useCallback((sessionId: string, stored: StoredMessage[]) => {
    dispatch({ type: "LOAD_SESSION", sessionId, messages: storedToMessages(stored) });
  }, []);

  return {
    state,
    handleSSEEvent,
    addUserMessage,
    startAssistant,
    cancelLastStream,
    setStreaming,
    giveFeedback,
    resume,
    setDatasource,
    setUserId,
    setEnableDq,
    newChat,
    loadSession,
  };
}
