import type { PreviewReply } from '../api/types'
import styles from './PreviewBar.module.css'
import { formatTime } from './timeline'

/**
 * A cut or keep waiting for a go-ahead (F-44). The player and the timeline already show the
 * result; this says how much it removes and lets a person apply or discard it. When the
 * timeline moved on and the edit no longer applies, only Discard is left.
 */
export function PreviewBar({
  pending,
  busy,
  onAccept,
  onReject,
}: {
  pending: PreviewReply
  /** True while accepting or rejecting is in flight. */
  busy: boolean
  onAccept: () => void
  onReject: () => void
}) {
  const { preview, problem } = pending
  const removed = preview.duration_before - preview.duration_after
  return (
    <div className={styles.bar} role="group" aria-label="Preview of the proposed edit">
      <p className={styles.summary}>
        {problem ? (
          <span role="alert">{problem} It can only be discarded.</span>
        ) : (
          <>
            <strong>Previewing: nothing is applied yet.</strong>{' '}
            <span className={styles.numbers}>
              {formatTime(preview.duration_before)} → {formatTime(preview.duration_after)}
              {removed > 0 && ` (${formatTime(removed)} removed)`}
            </span>
          </>
        )}
      </p>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.apply}
          disabled={busy || !!problem}
          onClick={onAccept}
        >
          Apply
        </button>
        <button type="button" className={styles.discard} disabled={busy} onClick={onReject}>
          Discard
        </button>
      </div>
    </div>
  )
}
