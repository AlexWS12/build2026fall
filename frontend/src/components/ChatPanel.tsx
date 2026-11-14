import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { StepReport } from '../api/types'
import type { ChatEntry } from '../state/session'
import { Panel } from './Panel'
import styles from './ChatPanel.module.css'

const SUGGESTIONS = ['blur faces', 'cut the first 5 seconds', 'mute the first 5 seconds']

type AgentStatus = Extract<ChatEntry, { kind: 'agent' }>['status']

const STATUS_NOTE: Record<AgentStatus, string | null> = {
  applied: null,
  clarify: 'A question. Nothing was changed.',
  not_applied: 'Nothing was changed.',
}

const icon = (children: ReactNode) => (
  <svg className={styles.icon} width="17" height="17" viewBox="0 0 24 24" aria-hidden="true">
    {children}
  </svg>
)

const STATUS_ICON: Record<AgentStatus | 'error', ReactNode> = {
  applied: icon(<path d="M20 6 9 17l-5-5" />),
  clarify: icon(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7" />
      <path d="M12 17h.01" />
    </>,
  ),
  not_applied: icon(
    <>
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.3 3.9 2.4 17.5a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </>,
  ),
  error: icon(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m15 9-6 6" />
      <path d="m9 9 6 6" />
    </>,
  ),
}

export function ChatPanel({
  entries,
  disabled,
  busy,
  undone,
  focusedRequest,
  className,
  onSend,
  onFocusRequest,
}: {
  entries: ChatEntry[]
  /** True until there's a project to edit. */
  disabled: boolean
  /** What's in flight, as one line, or null when a request can be sent. */
  busy: string | null
  /** Request ids that have been undone since. */
  undone?: ReadonlySet<string>
  focusedRequest?: string | null
  className?: string
  onSend: (text: string) => void
  onFocusRequest?: (requestId: string | null) => void
}) {
  const [draft, setDraft] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const end = useRef<HTMLLIElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'nearest' })
  }, [entries.length, busy])

  // "/" jumps to the request box from anywhere that isn't already a text field.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName))) return
      event.preventDefault()
      input.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  return (
    <Panel title="Chat" className={className}>
      <div className={styles.chat}>
        <ol className={styles.messages} aria-label="Messages">
          {entries.map((entry) => (
            <Message
              key={entry.id}
              entry={entry}
              undone={entry.kind === 'agent' && !!undone?.has(entry.requestId)}
              focused={entry.kind === 'agent' && entry.requestId === focusedRequest}
              onFocusRequest={onFocusRequest}
            />
          ))}
          {busy && <li className={`${styles.agent} ${styles.pending}`}>{busy}</li>}
          <li ref={end} aria-hidden="true" />
        </ol>
        {!disabled && (
          <div className={styles.suggestions} aria-label="Examples">
            {SUGGESTIONS.map((text) => (
              <button
                key={text}
                type="button"
                onClick={() => {
                  setDraft(text)
                  input.current?.focus()
                }}
              >
                {text}
              </button>
            ))}
          </div>
        )}
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault()
            const text = draft.trim()
            if (!text || busy) return
            onSend(text)
            setDraft('')
          }}
        >
          <input
            ref={input}
            aria-label="Request"
            placeholder={disabled ? 'Upload a video first' : 'Tell the editor what to change'}
            value={draft}
            disabled={disabled}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" aria-label="Send" disabled={disabled || !!busy || !draft.trim()}>
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 19V5" />
              <path d="m5 12 7-7 7 7" />
            </svg>
          </button>
        </form>
        <p className={styles.hint}>Enter sends. Press / to jump here.</p>
      </div>
    </Panel>
  )
}

function Message({
  entry,
  undone,
  focused,
  onFocusRequest,
}: {
  entry: ChatEntry
  undone: boolean
  focused: boolean
  onFocusRequest?: (requestId: string | null) => void
}) {
  if (entry.kind === 'user') return <li className={styles.user}>{entry.text}</li>
  if (entry.kind === 'note') return <li className={styles.note}>{entry.text}</li>
  if (entry.kind === 'error') {
    return (
      <li className={`${styles.agent} ${styles.error}`} role="alert">
        <div className={styles.line}>
          {STATUS_ICON.error}
          <span>{entry.text} Nothing was changed.</span>
        </div>
      </li>
    )
  }
  // The server's own refusals already end with it; don't say it twice.
  const said = STATUS_NOTE[entry.status]
  const note = undone ? 'Undone.' : said && !entry.text.includes(said) ? said : null
  return (
    <li
      className={`${styles.agent} ${styles[entry.status]}${focused ? ` ${styles.focused}` : ''}${
        undone ? ` ${styles.undone}` : ''
      }`}
      onMouseEnter={() => entry.status === 'applied' && onFocusRequest?.(entry.requestId)}
      onMouseLeave={() => entry.status === 'applied' && onFocusRequest?.(null)}
    >
      <div className={styles.line}>
        {STATUS_ICON[entry.status]}
        <span className={styles.text}>{entry.text}</span>
      </div>
      {note && <span className={styles.status}>{note}</span>}
      {entry.steps.length > 0 && (
        <details className={styles.steps}>
          <summary>
            {entry.steps.length} {entry.steps.length === 1 ? 'step' : 'steps'}
          </summary>
          <ol>
            {entry.steps.map((step) => (
              <li key={step.step_id} className={step.ok ? undefined : styles.failedStep}>
                <code>{describeStep(step)}</code>
              </li>
            ))}
          </ol>
        </details>
      )}
    </li>
  )
}

/** One tool call as `tool(args) → result`, the raw view behind the plain-words reply (F-40). */
function describeStep(step: StepReport): string {
  const call = `${step.tool}(${JSON.stringify(step.args)})`
  if (!step.ok) return `${call} → refused`
  return step.result ? `${call} → ${JSON.stringify(step.result)}` : call
}
