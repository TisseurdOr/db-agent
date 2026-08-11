import { useCallback, useRef } from "react";
import type { SSEEvent } from "../types";

interface UseSSEOptions {
  onEvent: (event: SSEEvent) => void;
  onError?: (error: string) => void;
  onDone?: () => void;
}

export function useSSE({ onEvent, onError, onDone }: UseSSEOptions) {
  const abortRef = useRef<AbortController | null>(null);

  const connect = useCallback(
    async (url: string, body: unknown) => {
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });

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
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== "AbortError") {
          onError?.(err.message);
        }
      } finally {
        onDone?.();
      }
    },
    [onEvent, onError, onDone],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { connect, abort };
}
