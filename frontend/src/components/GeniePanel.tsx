// The Genie assistant: a floating FAB that expands into a chat panel.
//
// The conversation id lives in a ref rather than state because it is threaded back
// into the next request but never rendered — keeping it out of state avoids a
// re-render on every reply for a value nothing displays.

import { useRef, useState } from 'react'
import { Bot, Send, Sparkles, X } from 'lucide-react'

import { api } from '../api'

interface Message {
  role: 'assistant' | 'user'
  text: string
  sql?: string | null
}

const SEED: Message = {
  role: 'assistant',
  text: 'Ask me about your use-case portfolio — value, readiness, dependencies, cross-LOB opportunities.',
}

export function GeniePanel() {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([SEED])
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const conversationId = useRef<string | undefined>(undefined)

  const send = async () => {
    const trimmed = question.trim()
    if (!trimmed || loading) return
    setMessages((current) => [...current, { role: 'user', text: trimmed }])
    setQuestion('')
    setLoading(true)
    try {
      const response = await api.genieAsk(trimmed, conversationId.current)
      if (response.conversation_id) conversationId.current = response.conversation_id
      setMessages((current) => [
        ...current,
        { role: 'assistant', text: response.answer, sql: response.sql },
      ])
    } catch {
      setMessages((current) => [
        ...current,
        { role: 'assistant', text: 'Sorry — the assistant is unavailable right now.' },
      ])
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      {!open ? (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-5 right-5 z-40 w-14 h-14 rounded-full flex items-center justify-center shadow-card-hover"
          style={{ background: '#FF3621' }}
          title="Ask Genie"
        >
          <Sparkles className="w-6 h-6 text-white" />
        </button>
      ) : null}

      {open ? (
        <div
          className="fixed bottom-5 right-5 z-40 w-[360px] max-w-[92vw] card p-0 overflow-hidden flex flex-col animate-scale-in"
          style={{ height: 460 }}
        >
          <div
            className="flex items-center justify-between px-4 py-3 border-b border-navy-600"
            style={{ background: '#143D4A' }}
          >
            <div className="flex items-center gap-2 text-white font-semibold text-sm">
              <Bot className="w-4 h-4 text-lava-300" /> Genie Assistant
            </div>
            <button
              aria-label="Close assistant"
              className="text-navy-400 hover:text-white"
              onClick={() => setOpen(false)}
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {messages.map((message, index) => (
              <div key={index} className={`text-sm ${message.role === 'user' ? 'text-right' : ''}`}>
                <div
                  className={`inline-block px-3 py-2 rounded-lg ${
                    message.role === 'user'
                      ? 'bg-lava/20 text-lava-300'
                      : 'bg-navy-700 text-navy-300'
                  }`}
                  style={{ maxWidth: '90%' }}
                >
                  {message.text}
                  {message.sql ? (
                    <pre className="mt-1 text-xs text-navy-400 whitespace-pre-wrap font-mono">
                      {message.sql}
                    </pre>
                  ) : null}
                </div>
              </div>
            ))}
            {loading ? <div className="text-sm text-navy-500">Thinking…</div> : null}
          </div>

          <div className="p-3 border-t border-navy-600 flex gap-2">
            <input
              id="genie-question"
              name="genie-question"
              aria-label="Ask the Genie assistant a question"
              className="input-field"
              placeholder="Ask a question…"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') send()
              }}
            />
            <button
              aria-label="Send"
              className="btn-primary px-3"
              disabled={loading || !question.trim()}
              onClick={send}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}
