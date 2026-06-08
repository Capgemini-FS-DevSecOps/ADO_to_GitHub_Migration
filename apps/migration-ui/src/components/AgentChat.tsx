'use client';

import { useRef, useState } from 'react';

const MODELS = [
  { id: 'claude-sonnet', label: 'Claude Sonnet', provider: 'Anthropic' },
  { id: 'claude-opus', label: 'Claude Opus', provider: 'Anthropic' },
  { id: 'gpt-4o', label: 'GPT-4o', provider: 'OpenAI' },
  { id: 'gpt-4.1', label: 'GPT-4.1', provider: 'OpenAI' },
  { id: 'gemini-pro', label: 'Gemini Pro', provider: 'Google' },
  { id: 'gemini-ultra', label: 'Gemini Ultra', provider: 'Google' },
];

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  model?: string;
};

const PLACEHOLDER_REPLY =
  'Agent backend is not connected yet. This tab is a placeholder for the PEV migration agent — ' +
  'model selection and chat layout will wire to the accelerator agent service in a future release.';

export function AgentChat() {
  const [modelId, setModelId] = useState(MODELS[0].id);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        'Migration agent placeholder. Ask about phase planning, pipeline conversion, or validation — ' +
        'responses are simulated until the agent service is enabled.',
      model: MODELS[0].id,
    },
  ]);
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const selectedModel = MODELS.find((m) => m.id === modelId) ?? MODELS[0];

  const send = () => {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: 'user', content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setTimeout(() => {
      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: PLACEHOLDER_REPLY,
          model: modelId,
        },
      ]);
      setSending(false);
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' });
    }, 400);
  };

  return (
    <div className="agent-chat-shell">
      <div className="agent-model-bar">
        <label htmlFor="agent-model-select" className="agent-model-label">
          Model
        </label>
        <select
          id="agent-model-select"
          className="oai-input agent-model-select"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
        >
          {MODELS.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} · {m.provider}
            </option>
          ))}
        </select>
        <span className="agent-model-hint">Placeholder — no live inference</span>
      </div>

      <div className="agent-chat-messages" ref={listRef}>
        {messages.map((msg) => (
          <div key={msg.id} className={`agent-chat-bubble agent-chat-${msg.role}`}>
            <div className="agent-chat-meta">
              <span>{msg.role === 'user' ? 'You' : selectedModel.label}</span>
              {msg.model && msg.role === 'assistant' && (
                <span className="agent-chat-model-tag">{msg.model}</span>
              )}
            </div>
            <p>{msg.content}</p>
          </div>
        ))}
        {sending && (
          <div className="agent-chat-bubble agent-chat-assistant agent-chat-typing">
            <span className="agent-typing-dots">Thinking</span>
          </div>
        )}
      </div>

      <div className="agent-chat-composer">
        <textarea
          className="oai-input agent-chat-input"
          rows={2}
          placeholder="Ask the migration agent…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button
          type="button"
          className="oai-button oai-button-primary agent-chat-send"
          disabled={!input.trim() || sending}
          onClick={send}
        >
          Send
        </button>
      </div>
    </div>
  );
}
