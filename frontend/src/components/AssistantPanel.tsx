// The one assistant: a floating FAB that expands into a chat panel.
//
// TIER3 PHASE 11 — CONSOLIDATION
// ------------------------------
// The app used to ship two assistants: this floating panel spoke to Genie's
// read-only Q&A (`/api/genie/ask`), while the operator console had its own
// tool-calling chat. Phase 11 unifies them onto the more-capable `/api/chat`
// loop and keeps this floating-FAB form factor. `/api/genie/ask` stays live
// server-side for a future SQL-mode toggle; this client no longer calls it.
//
// WHY `/api/chat` CHANGES THE UI'S SHAPE
// --------------------------------------
// Genie answered questions. `/api/chat` can also PROPOSE A WRITE: when the model
// calls a write tool, the server does NOT apply it — it returns a single-use
// confirm token in `response.confirm`. So this panel cannot just print an answer;
// a turn may come back with a change awaiting approval, which is fed straight into
// the shared `<ConfirmCard>`. Nothing a chat proposes is auto-applied — it lands
// in exactly the same human-approval gate as a change proposed anywhere else.
//
// The conversation id lives in a ref rather than state because it is threaded back
// into the next request but never rendered — keeping it out of state avoids a
// re-render on every reply for a value nothing displays.

import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Bot, Send, Sparkles, X } from 'lucide-react'

import { api } from '../api'
import { NO_RETRY } from '../lib/retry'
import { isLimited, messageOf, retryHint } from '../lib/errors'
import type { ChatResponse, ConfirmCardData } from '../types'
import { ConfirmCard } from './ConfirmCard'
import { useApiErrorToast } from './Toasts'

interface Message {
  role: 'assistant' | 'user'
  text: string
  /** A write the model proposed this turn. Rendered as a gated <ConfirmCard>. */
  confirm?: ConfirmCardData | null
  /** A non-error advisory from the server (e.g. the tool loop hit its bound). */
  note?: string | null
}

/** Used only when the failure carried no message at all — an offline request. */
const FALLBACK = 'Sorry — the assistant is unavailable right now.'

const SEED: Message = {
  role: 'assistant',
  text:
    'Ask me about your use-case portfolio — value, readiness, dependencies, ' +
    'cross-LOB opportunities. I can also propose changes, which you approve before they apply.',
}

interface AssistantPanelProps {
  /** Hide the FAB and panel when a use-case detail overlay is open. */
  hidden?: boolean
}

export function AssistantPanel({ hidden = false }: AssistantPanelProps) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<Message[]>([SEED])
  const [question, setQuestion] = useState('')
  const conversationId = useRef<string | undefined>(undefined)
  const toastError = useApiErrorToast()
  const queryClient = useQueryClient()

  // Close the panel when hidden becomes true (e.g., a use-case detail opens).
  // This ensures the panel doesn't linger over the drawer/page overlay.
  useEffect(() => {
    if (hidden && open) {
      setOpen(false)
    }
  }, [hidden, open])

  // A confirmed chat write can touch anything the assistant's tools reach, and the
  // panel cannot know which. Invalidating the broad portfolio keys is the same
  // conservative refresh the generation surface does after an apply — a chat write
  // is the least predictable caller, so it refreshes the most.
  const invalidateAfterWrite = () => {
    for (const key of ['use-cases', 'portfolio-value', 'coverage-matrix', 'data-assets']) {
      queryClient.invalidateQueries({ queryKey: [key] })
    }
  }

  const send = useMutation({
    // NO_RETRY: a chat turn spends real tokens and runs a whole tool-calling loop.
    // An automatic second POST after a 429 spends the budget the `Retry-After`
    // asked us to wait out and double-bills the turn. See `lib/retry.ts`.
    ...NO_RETRY,
    mutationFn: (text: string): Promise<ChatResponse> =>
      api.chat(text, conversationId.current),
    onSuccess: (response) => {
      if (response.conversation_id) conversationId.current = response.conversation_id
      setMessages((current) => [
        ...current,
        {
          role: 'assistant',
          text: response.answer,
          confirm: response.confirm ?? null,
          note: response.note ?? null,
        },
      ])
    },
    onError: (error) => {
      // Say what actually happened. The `chat` limit's 429 body is written to be
      // shown verbatim (`server/limits.py`), so discarding it throws away the one
      // sentence that says what to do. Two surfaces on purpose: the reason goes in
      // the transcript next to the question it failed to answer, and a toast covers
      // the case where the panel was closed while the request was in flight.
      const reason = messageOf(error, FALLBACK)
      const hint = isLimited(error) ? retryHint(error) : null
      setMessages((current) => [
        ...current,
        { role: 'assistant', text: hint ? `${reason} Try again ${hint}.` : reason },
      ])
      toastError(error, FALLBACK)
    },
  })

  const submit = () => {
    const trimmed = question.trim()
    if (!trimmed || send.isPending) return
    setMessages((current) => [...current, { role: 'user', text: trimmed }])
    setQuestion('')
    send.mutate(trimmed)
  }

  // Don't render anything when hidden (e.g., a use-case detail overlay is open).
  if (hidden) return null

  return (
    <>
      {!open ? (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-5 right-5 z-40 w-14 h-14 rounded-full flex items-center justify-center shadow-card-hover"
          style={{ background: '#FF3621' }}
          title="Ask the assistant"
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
              <Bot className="w-4 h-4 text-lava-300" /> Assistant
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
                  {message.note ? (
                    <div className="mt-1 text-xs text-navy-400">{message.note}</div>
                  ) : null}
                </div>
                {/* A proposed write is confirmed here before it runs — the server
                    issued a single-use token and applied nothing. */}
                {message.confirm ? (
                  <div className="mt-2 text-left">
                    <ConfirmCard
                      token={message.confirm.token}
                      data={message.confirm}
                      onApplied={invalidateAfterWrite}
                    />
                  </div>
                ) : null}
              </div>
            ))}
            {send.isPending ? <div className="text-sm text-navy-500">Thinking…</div> : null}
          </div>

          <div className="p-3 border-t border-navy-600 flex gap-2">
            <input
              id="assistant-question"
              name="assistant-question"
              aria-label="Ask the assistant a question"
              className="input-field"
              placeholder="Ask a question…"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') submit()
              }}
            />
            <button
              aria-label="Send"
              className="btn-primary px-3"
              disabled={send.isPending || !question.trim()}
              onClick={submit}
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}
