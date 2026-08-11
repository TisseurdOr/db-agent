import { useReducer, useCallback } from "react";
import type { ChatState, Message, SSEEvent, InterruptData, ChartConfig } from "../types";

type Action =
  | { type: "ADD_USER_MESSAGE"; content: string }
  | { type: "START_ASSISTANT_MESSAGE" }
  | { type: "STEP_START"; node: string; task: string }
  | { type: "STEP_END"; node: string; task: string; elapsed: number; tokens: number }
  | { type: "TEXT_DELTA"; text: string }
  | { type: "SET_STREAMING"; isStreaming: boolean }
  | { type: "DONE"; traceId: string; opikTraceId?: string; sql: string; answer: string; charts: ChartConfig[]; plan: { agent: string; task: string }[] }
  | { type: "ERROR"; message: string }
  | { type: "INTERRUPT"; data: InterruptData }
  | { type: "RESUME" }
  | { type: "FEEDBACK"; messageId: string; rating: "up" | "down" }
  | { type: "SET_DATASOURCE"; datasource: string };

function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function getLastAssistant(state: ChatState): Message | null {
  for (let i = state.messages.length - 1; i >= 0; i--) {
    if (state.messages[i].role === "assistant") return state.messages[i];
  }
  return null;
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
      };
      return {
        ...state,
        messages: [...state.messages, assistantMsg],
        isStreaming: true,
      };
    }

    case "STEP_START": {
      const messages = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        last.steps = [
          ...last.steps,
          { node: action.node, task: action.task, status: "running" as const },
        ];
      }
      return { ...state, messages };
    }

    case "STEP_END": {
      const messages = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        const step = last.steps.find(
          (s) => s.node === action.node && s.status === "running",
        );
        if (step) {
          step.status = "done";
          step.elapsed = action.elapsed;
          step.tokens = action.tokens;
        }
      }
      return { ...state, messages };
    }

    case "TEXT_DELTA": {
      const messages = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        last.content += action.text;
      }
      return { ...state, messages };
    }

    case "SET_STREAMING": {
      return { ...state, isStreaming: action.isStreaming };
    }

    case "DONE": {
      const messages = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        last.isStreaming = false;
        last.traceId = action.traceId;
        last.opikTraceId = action.opikTraceId || "";
        last.sql = action.sql;
        last.charts = action.charts;
        if (action.answer && !last.content) {
          last.content = action.answer;
        }
        // Add plan steps that weren't captured by step events
        for (const p of action.plan || []) {
          if (!last.steps.find((s) => s.node === p.agent)) {
            last.steps.push({
              node: p.agent,
              task: p.task,
              status: "done",
            });
          }
        }
      }
      return { ...state, messages, isStreaming: false, hitlActive: false };
    }

    case "ERROR": {
      const messages = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        last.content = `❌ ${action.message}`;
        last.isStreaming = false;
      }
      return { ...state, messages, isStreaming: false, hitlActive: false };
    }

    case "INTERRUPT": {
      const msgs = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        last.hitl = action.data;
      }
      return { ...state, messages: msgs, isStreaming: false, hitlActive: true, hitlData: action.data };
    }

    case "RESUME": {
      const msgs = [...state.messages];
      const last = getLastAssistant(state);
      if (last) {
        last.hitl = undefined;
        last.isStreaming = true;
      }
      return { ...state, messages: msgs, isStreaming: true, hitlActive: false, hitlData: null };
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

    default:
      return state;
  }
}

const initialState: ChatState = {
  messages: [],
  isStreaming: false,
  datasource: "sqlite",
  sessionId: "default",
  hitlActive: false,
  hitlData: null,
};

export function useChat() {
  const [state, dispatch] = useReducer(chatReducer, initialState);

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    const raw = event as unknown as Record<string, unknown>;
    const etype = (raw._eventType as string) || event.type;
    switch (etype) {
      case "connected":
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

  return {
    state,
    handleSSEEvent,
    addUserMessage,
    startAssistant,
    setStreaming,
    giveFeedback,
    resume,
    setDatasource,
  };
}
