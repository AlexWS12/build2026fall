import type { ReviewItem, ReviewReply, Track } from '../api/types'
import { Panel } from './Panel'
import styles from './ReviewPanel.module.css'
import { formatTime, trackNames } from './timeline'

const KIND_LABEL: Record<ReviewItem['kind'], string> = {
  lost: 'Target lost',
  no_track: 'No position data',
  uncertain: 'Unsure detection',
  gap: 'Detection gap',
}

/** The stretches of the masks to check (F-41): go to one, then confirm it as it is or cut it out. */
export function ReviewPanel({
  review,
  reviewError,
  tracks,
  busy,
  className,
  onSeek,
  onReview,
}: {
  review: ReviewReply | null
  /** Why the queue couldn't be read, when it couldn't. */
  reviewError: string | null
  tracks: Track[]
  /** True while the project is doing anything else; the server refuses a second request. */
  busy: boolean
  className?: string
  onSeek: (seconds: number) => void
  onReview: (itemId: string, action: 'confirm' | 'cut') => void
}) {
  const names = trackNames(tracks)
  const nothing = review !== null && review.items.length === 0
  return (
    <Panel title="Review" className={className}>
      {reviewError && <p role="alert">Couldn't load the review queue: {reviewError}</p>}
      {review && !review.analysed && (
        <p className={styles.warning}>
          This video hasn't been analysed, so masks on detected targets cover the whole frame.
          Analyse it to review them.
        </p>
      )}
      {review && review.items.length > 0 && (
        <ol className={styles.items} aria-label="Review items">
          {review.items.map((item) => {
            const range = `${formatTime(item.start)}–${formatTime(item.end)}`
            const goTo = `Go to ${formatTime(item.start)}`
            return (
              <li key={item.item_id} className={styles.item}>
                <button
                  type="button"
                  className={styles.row}
                  title={goTo}
                  aria-label={`${goTo}: ${KIND_LABEL[item.kind]}, ${range}`}
                  onClick={() => onSeek(item.start)}
                >
                  <span className={styles.kind}>{KIND_LABEL[item.kind]}</span>
                  <span className={styles.track}>
                    {names.get(item.track_id) ?? item.label ?? item.track_id}
                  </span>
                  <span className={styles.range}>{range}</span>
                </button>
                <button
                  type="button"
                  className={styles.button}
                  disabled={busy}
                  aria-label={`Confirm ${range}`}
                  onClick={() => onReview(item.item_id, 'confirm')}
                >
                  Confirm
                </button>
                <button
                  type="button"
                  className={styles.button}
                  disabled={busy}
                  aria-label={`Cut ${range}`}
                  onClick={() => onReview(item.item_id, 'cut')}
                >
                  Cut
                </button>
              </li>
            )
          })}
        </ol>
      )}
      {review && nothing && review.analysed && <p className={styles.hint}>Nothing to check.</p>}
      {review && review.held_seconds > 0 && nothing && (
        <p className={styles.hint}>
          {review.held_seconds.toFixed(1)} s of boxes held just past a track's end aren't listed.
        </p>
      )}
      {review && review.rules_not_audited.length > 0 && (
        <p className={styles.hint}>Standing rules can't be checked here.</p>
      )}
    </Panel>
  )
}
