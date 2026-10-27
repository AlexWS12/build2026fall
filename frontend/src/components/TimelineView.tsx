import type { TimelineState } from '../api/types'
import { Panel } from './Panel'
import { fallbackDuration, formatTime, lanesOf } from './timeline'
import styles from './TimelineView.module.css'

export function TimelineView({
  state,
  duration,
  className,
}: {
  state: TimelineState | null
  /** Seconds of source video, from the player. Null until the video's metadata loads. */
  duration: number | null
  className?: string
}) {
  if (!state) {
    return (
      <Panel title="Timeline" className={className}>
        <p className={styles.empty}>No edits yet.</p>
      </Panel>
    )
  }

  const total = duration ?? fallbackDuration(state)
  const percent = (t: number) => `${Math.min(100, (t / total) * 100)}%`
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * total)

  return (
    <Panel title="Timeline" className={className}>
      <div className={styles.timeline}>
        <div className={styles.axis} aria-hidden="true">
          {ticks.map((t) => (
            <span key={t} style={{ left: percent(t) }}>
              {formatTime(t)}
            </span>
          ))}
        </div>
        <ul className={styles.lanes} aria-label="Timeline lanes">
          {lanesOf(state).map((lane) => (
            <li key={lane.name} className={styles.lane}>
              <span className={styles.laneName}>{lane.name}</span>
              <span className={styles.track}>
                {lane.spans.map((span) => {
                  const end = span.end ?? total
                  return (
                    <span
                      key={`${span.start}-${span.label}`}
                      className={`${styles.span} ${styles[lane.kind]}`}
                      style={{ left: percent(span.start), width: percent(end - span.start) }}
                      title={`${span.label}: ${formatTime(span.start)}–${
                        span.end === null ? 'end' : formatTime(span.end)
                      }`}
                    />
                  )
                })}
              </span>
            </li>
          ))}
        </ul>
        {state.rules.length > 0 && (
          <p className={styles.rules}>
            Standing rules:{' '}
            {state.rules
              .map((r) => `mask every ${r.category} except ${r.except_identities.join(', ') || 'nobody'}`)
              .join('; ')}
          </p>
        )}
      </div>
    </Panel>
  )
}
