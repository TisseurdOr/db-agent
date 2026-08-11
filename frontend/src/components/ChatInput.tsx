import { useState, useRef, useEffect } from "react";

interface Props {
  onSend: (query: string) => void;
  disabled: boolean;
}

export default function ChatInput({ onSend, disabled }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!disabled && ref.current) ref.current.focus();
  }, [disabled]);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  };

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="chat-input" style={{ display: "flex", gap: 8, padding: "12px 16px", borderTop: "1px solid #333" }}>
      <textarea
        ref={ref}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKey}
        placeholder="输入查询，如「华东地区上个月销售额最高的产品」"
        disabled={disabled}
        rows={2}
        style={{
          flex: 1, resize: "none", padding: "10px 14px", borderRadius: 10,
          border: "1px solid #444", background: "#1a1a2e", color: "#ddd",
          fontSize: 14, fontFamily: "inherit", outline: "none",
        }}
      />
      <button
        onClick={handleSend}
        disabled={disabled || !text.trim()}
        style={{
          padding: "8px 20px", borderRadius: 10, border: "none",
          background: disabled ? "#333" : "#4a6cf7", color: "#fff",
          fontSize: 14, fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer",
          whiteSpace: "nowrap",
        }}
      >
        发送
      </button>
    </div>
  );
}
