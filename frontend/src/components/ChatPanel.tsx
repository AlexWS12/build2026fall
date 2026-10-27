import { useState } from 'react'
import { Panel } from './Panel'
import styles from './ChatPanel.module.css'

export interface ChatMessage {
  id: string
  from: 'user' | 'agent'
  text: string
}

export function ChatPanel({
  messages,
  disabled,
  busy,
  className,
  onSend,
}: {
  messages: ChatMessage[]
  /** True until there's a project to edit. */
  disabled: boolean
  busy: boolean
  className?: string
  onSend: (text: string) => void
}) {
  const [draft, setDraft] = useState('')

  return (
    <Panel title="Chat" className={className}>
      <div className={styles.chat}>
        <ol className={styles.messages} aria-label="Messages">
          {messages.map((message) => (
            <li key={message.id} className={styles[message.from]}>
              {message.text}
            </li>
          ))}
          {busy && <li className={styles.agent}>Thinking…</li>}
        </ol>
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault()
            const text = draft.trim()
            if (!text) return
            onSend(text)
            setDraft('')
          }}
        >
          <input
            aria-label="Request"
            placeholder={disabled ? 'Upload a video first' : 'e.g. blur faces for the first half'}
            value={draft}
            disabled={disabled}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" disabled={disabled || busy || !draft.trim()}>
            Send
          </button>
        </form>
      </div>
    </Panel>
  )
}
