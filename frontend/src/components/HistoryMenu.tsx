import { useEffect, useId, useRef, useState } from 'react'
import type { HistoryReply } from '../api/types'
import { undoTarget } from '../state/session'
import { kindsLabel, requestLabel } from './history'
import styles from './HistoryMenu.module.css'

/**
 * Every request that changed the video, newest first. Read-only: undo is linear, so the list
 * shows which request Undo would take back rather than offering one to pick.
 */
export function HistoryList({ history }: { history: HistoryReply }) {
  const next = undoTarget(history)
  if (history.requests.length === 0) {
    return <p className={styles.empty}>No edits yet. Requests that change the video are listed here.</p>
  }
  return (
    <ol className={styles.list} aria-label="Requests" reversed>
      {history.requests.toReversed().map((entry) => {
        const label = requestLabel(entry)
        const undone = entry.status === 'undone'
        return (
          <li key={entry.request_id} className={undone ? styles.undone : undefined}>
            <span className={styles.label} title={label}>
              {label}
            </span>
            {undone ? (
              <span className={styles.status}>Undone</span>
            ) : entry.request_id === next ? (
              <span className={styles.next}>Undo next</span>
            ) : (
              entry.text && <span className={styles.status}>{kindsLabel(entry)}</span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

/** The History button in the header, and the list it opens under itself. */
export function HistoryMenu({ history }: { history: HistoryReply }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      // Undo and redo sit beside the button: pressing them keeps the list open, to watch it change.
      const keep = root.current?.closest('[role="toolbar"]') ?? root.current
      if (!keep?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const count = history.requests.length
  return (
    <div className={styles.root} ref={root}>
      <button
        type="button"
        className={styles.button}
        aria-label="History"
        title="Request history"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((was) => !was)}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
          <path d="M3 3v5h5" />
          <path d="M12 7v5l4 2" />
        </svg>
      </button>
      {open && (
        <section id={panelId} className={styles.panel} aria-label="Request history">
          <h2 className={styles.title}>
            History
            {count > 0 && (
              <span className={styles.count}>
                {count} {count === 1 ? 'request' : 'requests'}
              </span>
            )}
          </h2>
          <HistoryList history={history} />
        </section>
      )}
    </div>
  )
}
