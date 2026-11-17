import { type KeyboardEvent, type RefObject, useEffect, useId, useRef, useState } from 'react'
import type { HistoryReply } from '../api/types'
import { redoTarget, undoneForGood, undoTarget } from '../state/session'
import { kindsLabel, requestLabel } from './history'
import styles from './HistoryMenu.module.css'

const FOR_GOOD_HINT = "A newer request came after this one, so Redo can't bring it back."

/**
 * Every request that changed the video, newest first. Read-only: undo is linear, so the list
 * shows which request Undo or Redo would act on rather than offering one to pick.
 *
 * `canUndo` and `canRedo` are the toolbar buttons' own state (they also need the project idle),
 * so the list never says "Undo next" or "Redo next" while the button beside it is disabled.
 * "Redo next" shows only when the history makes it certain (see `redoTarget`).
 */
export function HistoryList({
  history,
  canUndo,
  canRedo,
}: {
  history: HistoryReply
  canUndo: boolean
  canRedo: boolean
}) {
  const undoNext = canUndo ? undoTarget(history) : null
  const redoNext = canRedo ? redoTarget(history) : null
  const forGood = undoneForGood(history)
  if (history.requests.length === 0) {
    return <p className={styles.empty}>No edits yet. Requests that change the video are listed here.</p>
  }
  return (
    // Focusable so the keyboard can scroll a long session, and Escape works from inside it.
    <ol className={styles.list} aria-label="Requests" reversed tabIndex={0}>
      {history.requests.toReversed().map((entry) => {
        const label = requestLabel(entry)
        // Without chat text the label already names the kinds, so they aren't repeated.
        const kinds = entry.text ? kindsLabel(entry) : ''
        const id = entry.request_id
        const undone = entry.status === 'undone'
        const gone = undone && forGood.has(id)
        return (
          <li key={id} className={gone ? styles.forGood : undone ? styles.undone : undefined}>
            <div className={styles.line}>
              <span className={styles.label} title={label}>
                {label}
              </span>
              {!undone ? (
                id === undoNext && <span className={styles.next}>Undo next</span>
              ) : id === redoNext ? (
                <span className={styles.next}>Redo next</span>
              ) : gone ? (
                <span className={styles.status} title={FOR_GOOD_HINT}>
                  Undone for good
                </span>
              ) : (
                <span className={styles.status}>Undone</span>
              )}
            </div>
            {kinds && <span className={styles.kinds}>{kinds}</span>}
          </li>
        )
      })}
    </ol>
  )
}

/**
 * The History button in the header, and the list it opens under itself.
 *
 * `keepOpen` names elements outside the menu that don't close it, so the list can be watched
 * while they change it (the toolbar passes Undo and Redo). Anything else closes it: a press
 * elsewhere, or focus moving elsewhere (tabbing out). Escape closes it only from inside the
 * menu, so Escape typed in the chat box doesn't, and it puts focus back on the History button.
 */
export function HistoryMenu({
  history,
  canUndo,
  canRedo,
  keepOpen = [],
  buttonClassName,
}: {
  history: HistoryReply
  canUndo: boolean
  canRedo: boolean
  keepOpen?: readonly RefObject<HTMLElement | null>[]
  /** The button's shape, so it matches its neighbours (the toolbar passes its `.icon`). */
  buttonClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const keeps = (node: Node) =>
      !!root.current?.contains(node) || keepOpen.some((ref) => ref.current?.contains(node))
    const close = (event: Event) => {
      if (!keeps(event.target as Node)) setOpen(false)
    }
    // focusin only fires when something takes focus, so a press on plain text in the list (or a
    // click that doesn't focus, as in Safari) is left to pointerdown.
    document.addEventListener('pointerdown', close)
    document.addEventListener('focusin', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('focusin', close)
    }
  }, [open, keepOpen])

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || !open) return
    setOpen(false)
    button.current?.focus()
  }

  const count = history.requests.length
  return (
    <div className={styles.root} ref={root} onKeyDown={onKeyDown}>
      <button
        ref={button}
        type="button"
        className={buttonClassName ? `${buttonClassName} ${styles.button}` : styles.button}
        aria-label="History"
        title="Request history"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((was) => !was)}
      >
        {/* Stroke set here, not inherited from `.toolbar svg`, so the clock draws anywhere. */}
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.2}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
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
          <HistoryList history={history} canUndo={canUndo} canRedo={canRedo} />
        </section>
      )}
    </div>
  )
}
