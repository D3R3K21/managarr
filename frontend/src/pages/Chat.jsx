import React, { useState, useRef, useEffect } from "react";
import api from "../api/client";
import { useApi } from "../hooks/useApi";
import PageHeader from "../components/PageHeader";
import { IconChat } from "../components/icons";

export default function Chat() {
  const tools = useApi("/chat/tools");
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      text:
        "Hi — I'm the in-app Managarr assistant (read-only fallback). The full conversational assistant runs through an MCP client connected to the Managarr MCP server. Ask about your library, missing items, health, or releases.",
    },
  ]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const logRef = useRef(null);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [messages, sending]);

  async function send(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    setSending(true);
    try {
      const res = await api.post("/chat", { message: text });
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          text: res.note || "(no response)",
          tool: res.suggested_tool,
          ran: res.ran,
          result: res.result,
        },
      ]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", text: `Error: ${err.message}`, error: true }]);
    } finally {
      setSending(false);
    }
  }

  const toolList = tools.data?.tools || [];

  return (
    <div>
      <PageHeader title="Chat" subtitle="In-app assistant — full agent runs via an MCP client" />

      {toolList.length > 0 && (
        <div className="card mb-3">
          <div className="section-title">Available MCP tools</div>
          <div className="row-wrap">
            {toolList.map((tool) => {
              const name = tool.name || tool;
              const write = tool.write || (tool.annotations && tool.annotations.write);
              return (
                <span className="tool-chip" key={name} title={tool.description || ""}>
                  <IconChat size={12} /> {name}
                  {write && <span className="badge badge-warn" style={{ fontSize: 9, padding: "0 4px" }}>write</span>}
                </span>
              );
            })}
          </div>
        </div>
      )}

      <div className="chat-wrap">
        <div className="chat-log" ref={logRef}>
          {messages.map((m, i) => (
            <div key={i} className={"chat-msg " + m.role}>
              <div>{m.text}</div>
              {m.tool && (
                <div className="faint mt-2" style={{ fontSize: 11 }}>
                  suggested tool: <span className="badge badge-accent">{m.tool}</span>
                  {m.ran ? " · ran" : ""}
                </div>
              )}
              {m.result != null && (
                <pre className="chat-result">{typeof m.result === "string" ? m.result : JSON.stringify(m.result, null, 2)}</pre>
              )}
            </div>
          ))}
          {sending && <div className="chat-msg assistant"><span className="muted">thinking…</span></div>}
        </div>

        <form className="chat-input-row" onSubmit={send}>
          <input
            className="input"
            placeholder="Ask about your library, health, missing items…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={sending}
          />
          <button className="btn btn-primary" type="submit" disabled={sending || !input.trim()}>Send</button>
        </form>
      </div>
    </div>
  );
}
