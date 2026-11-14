import { useEffect, useRef, useState } from 'react'
import type { StepReport } from '../api/types'
import type { ChatEntry } from '../state/session'
import { Panel } from './Panel'
import styles from './ChatPanel.module.css'

const SUGGESTIONS = ['blur faces', 'cut the first 5 seconds', 'mute the first 5 seconds']

const STATUS_NOTE: Record<Extract<ChatEntry, { kind: 'agent' }>['status'], string | null> = {
  applied: null,
  clarify: 'A question. Nothing was changed.',
  not_applied: 'Nothing was changed.',
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
        {!disabled && entries.length === 0 && (
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
            placeholder={disabled ? 'Upload a video first' : 'e.g. blur faces for the first half'}
            value={draft}
            disabled={disabled}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" disabled={disabled || !!busy || !draft.trim()}>
            Send
          </button>
        </form>
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
        {entry.text} Nothing was changed.
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
      <span>{entry.text}</span>
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
