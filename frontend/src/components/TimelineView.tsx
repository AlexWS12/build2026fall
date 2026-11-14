import type { MouseEvent } from 'react'
import type { TimelineState, Track } from '../api/types'
import { Panel } from './Panel'
import { outputDuration } from './preview'
import { fallbackDuration, formatTime, lanesOf } from './timeline'
import styles from './TimelineView.module.css'

export function TimelineView({
  state,
  duration,
  tracks,
  time,
  focusedRequest,
  className,
  onSeek,
  onFocusRequest,
}: {
  state: TimelineState | null
  /** Seconds of source video. Null until it's known. */
  duration: number | null
  /** Analysis tracks, to name mask lanes by what they are. */
  tracks?: Track[]
  /** The player's position in source seconds, drawn as the playhead. */
  time?: number
  /** Spans this request made stand out; the rest dim. */
  focusedRequest?: string | null
  className?: string
  onSeek?: (seconds: number) => void
  onFocusRequest?: (requestId: string | null) => void
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
  const output = outputDuration(state, total)

  function seekFromClick(event: MouseEvent<HTMLElement>) {
    if (!onSeek) return
    const { left, width } = event.currentTarget.getBoundingClientRect()
    if (width > 0) onSeek(Math.min(1, Math.max(0, (event.clientX - left) / width)) * total)
  }

  return (
    <Panel title="Timeline" className={className}>
      <div className={styles.timeline}>
        {state.cuts.length > 0 && duration !== null && (
          <p className={styles.summary}>
            {formatTime(total)} of source, {formatTime(output)} after cuts
          </p>
        )}
        <div className={styles.axis} aria-hidden="true">
          {ticks.map((t) => (
            <span key={t} style={{ left: percent(t) }}>
              {formatTime(t)}
            </span>
          ))}
        </div>
        <div className={styles.body}>
          <ul className={styles.lanes} aria-label="Timeline lanes">
            {lanesOf(state, tracks).map((lane) => (
              <li key={lane.id} className={styles.lane}>
                <span className={styles.laneName} title={lane.id}>
                  {lane.name}
                </span>
                <span className={styles.track} onClick={seekFromClick}>
                  {lane.spans.map((span) => {
                    const end = span.end ?? total
                    const dimmed =
                      !!focusedRequest && span.requestId !== null && span.requestId !== focusedRequest
                    return (
                      <span
                        key={`${span.start}-${span.label}`}
                        className={`${styles.span} ${styles[lane.kind]}${dimmed ? ` ${styles.dimmed}` : ''}`}
                        style={{ left: percent(span.start), width: percent(end - span.start) }}
                        title={`${span.label}: ${formatTime(span.start)}–${
                          span.end === null ? 'end' : formatTime(span.end)
                        }`}
                        onMouseEnter={() => span.requestId && onFocusRequest?.(span.requestId)}
                        onMouseLeave={() => span.requestId && onFocusRequest?.(null)}
                      />
                    )
                  })}
                </span>
              </li>
            ))}
          </ul>
          {time !== undefined && (
            <span className={styles.playheadRail} aria-hidden="true">
              <span className={styles.playhead} style={{ left: percent(time) }} />
            </span>
          )}
        </div>
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
