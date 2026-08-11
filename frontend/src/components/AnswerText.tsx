import { useState, useRef, useEffect } from "react";

interface Props {
  text: string;
  isStreaming: boolean;
}

export default function AnswerText({ text, isStreaming }: Props) {
  const [visibleLen, setVisibleLen] = useState(text.length);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    setVisibleLen(text.length);
  }, [text]);

  // Typewriter reveal for streaming text
  useEffect(() => {
    if (!isStreaming) {
      setVisibleLen(text.length);
      return;
    }
    if (visibleLen >= text.length) return;

    rafRef.current = requestAnimationFrame(() => {
      setVisibleLen((prev) => Math.min(prev + 3, text.length));
    });
    return () => cancelAnimationFrame(rafRef.current);
  }, [isStreaming, text, visibleLen]);

  const display = text.slice(0, isStreaming ? visibleLen : text.length);

  return (
    <div className="answer-text" style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>
      {display}
      {isStreaming && visibleLen < text.length && (
        <span className="cursor-blink">▌</span>
      )}
    </div>
  );
}
