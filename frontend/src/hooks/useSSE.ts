import { useCallback, useRef } from "react";
import type { SSEEvent } from "../types";

interface UseSSEOptions {
  onEvent: (event: SSEEvent) => void;
  onError?: (error: string) => void;
  onDone?: () => void;
  /** 网络断流时的重连次数（默认 3；HTTP 错误不重试） */
  reconnectAttempts?: number;
  /** 重连基础延迟 ms（指数退避：1s → 2s → 4s） */
  reconnectDelayMs?: number;
  /** 单次建连超时 ms（后端挂掉时避免「连接中」无限挂起） */
  connectTimeoutMs?: number;
}

export function useSSE({
  onEvent,
  onError,
  onDone,
  reconnectAttempts = 3,
  reconnectDelayMs = 1000,
  connectTimeoutMs = 15000,
}: UseSSEOptions) {
  const abortRef = useRef<AbortController | null>(null);

  /** 可被 abort 打断的等待，重连退避期间用户停止要能立即生效 */
  const sleep = useCallback((ms: number, signal: AbortSignal) => {
    return new Promise<void>((resolve, reject) => {
      const onAbort = () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      const timer = setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, ms);
      signal.addEventListener("abort", onAbort);
    });
  }, []);

  const connect = useCallback(
    async (url: string, body: unknown) => {
      // 新查询先打断旧 SSE，避免后端白烧 token、事件串到新气泡
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      let attempt = 0;

      try {
        while (true) {
          try {
            const timeoutCtrl = new AbortController();
            const connectTimer = setTimeout(() => timeoutCtrl.abort(), connectTimeoutMs);
            const signal =
              typeof AbortSignal !== "undefined" && "any" in AbortSignal
                ? AbortSignal.any([controller.signal, timeoutCtrl.signal])
                : controller.signal;

            let response: Response;
            try {
              response = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
                signal,
              });
            } finally {
              clearTimeout(connectTimer);
            }

            if (!response.ok) {
              onError?.(`HTTP ${response.status}: ${response.statusText}`);
              return;
            }

            const reader = response.body?.getReader();
            if (!reader) {
              onError?.("Response body is not readable");
              return;
            }

            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
              const { done, value } = await reader.read();
              if (done) break;

              buffer += decoder.decode(value, { stream: true });
              const lines = buffer.split("\n");
              buffer = lines.pop() || "";

              let currentEvent = "";
              for (const line of lines) {
                if (line.startsWith("event: ")) {
                  currentEvent = line.slice(7).trim();
                } else if (line.startsWith("data: ")) {
                  try {
                    const data = JSON.parse(line.slice(6));
                    data._eventType = currentEvent || data.type;
                    onEvent(data);
                  } catch {
                    // skip malformed JSON
                  }
                }
              }
            }
            return; // 服务端正常结束
          } catch (err: unknown) {
            // 用户主动中止：不重试
            if (controller.signal.aborted) return;
            // 超时 / 网络断流：指数退避重连
            attempt++;
            const msg = err instanceof Error ? err.message : String(err);
            if (attempt > reconnectAttempts) {
              onError?.(
                msg.includes("aborted") || msg.includes("AbortError")
                  ? "连接后端超时，请确认服务已启动（:8000）"
                  : msg,
              );
              return;
            }
            const delay = reconnectDelayMs * 2 ** (attempt - 1);
            try {
              await sleep(delay, controller.signal);
            } catch {
              return; // 等待期间被中止
            }
          }
        }
      } finally {
        onDone?.();
      }
    },
    [onEvent, onError, onDone, reconnectAttempts, reconnectDelayMs, connectTimeoutMs, sleep],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { connect, abort };
}
