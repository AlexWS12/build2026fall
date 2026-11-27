import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import type { TimelineState, Track } from '../api/types'
import { Panel } from './Panel'
import { outputDuration } from './preview'
import { fallbackDuration, formatTime, keptRange, lanesOf } from './timeline'
import styles from './TimelineView.module.css'

/** The trim handles move in tenths of a second, and never closer than that to each other. */
const TRIM_STEP = 0.1
/** Arrow keys move the playhead by this much; Page Up / Down by five times as much. */
const SEEK_STEP = 1

export function TimelineView({
  state,
  duration,
  tracks,
  time,
  focusedRequest,
  className,
  onSeek,
  onFocusRequest,
  onTrim,
  trimDisabled = false,
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
  /** Keep only `start`..`end` of the source (the trim handles). No handles without it. */
  onTrim?: (start: number, end: number) => void
  /** True while the project is busy: the handles stay put and Trim waits. */
  trimDisabled?: boolean
}) {
  const [handles, setHandles] = useState<{ key: string; start: number; end: number } | null>(null)
  /** The track area (lanes without their names): where a time is drawn, so where to read one. */
  const rail = useRef<HTMLSpanElement>(null)
  const scrubbing = useRef(false)

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

  // What earlier trims (cuts at either end) left: the handles start there and can't leave it,
  // since only undo brings cut footage back. Handles moved for another video or before another
  // edit changed that range start over.
  const [keptStart, keptEnd] = keptRange(state, total)
  const key = `${total}|${keptStart}|${keptEnd}`
  const trim = handles && handles.key === key ? handles : { key, start: keptStart, end: keptEnd }
  const pending = trim.start > keptStart || trim.end < keptEnd
  const alreadyTrimmed = keptStart > 0 || keptEnd < total
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
  const setTrim = (start: number, end: number) => setHandles({ key, start, end })

  const seekAt = (clientX: number) => {
    const box = rail.current?.getBoundingClientRect()
    if (!onSeek || !box || box.width <= 0) return
    onSeek(clamp((clientX - box.left) / box.width, 0, 1) * total)
  }

  // The playhead follows the pointer from the moment it goes down on the ruler or the lanes until
  // it's let go, even outside the timeline (pointer capture).
  const scrub = {
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (!onSeek || event.button !== 0) return
      scrubbing.current = true
      event.currentTarget.setPointerCapture?.(event.pointerId)
      seekAt(event.clientX)
    },
    onPointerMove(event: PointerEvent<HTMLElement>) {
      if (scrubbing.current) seekAt(event.clientX)
    },
    onPointerUp() {
      scrubbing.current = false
    },
    onPointerCancel() {
      scrubbing.current = false
    },
  }

  function seekByKey(event: KeyboardEvent<HTMLElement>) {
    if (!onSeek) return
    const now = time ?? 0
    const to: Record<string, number> = {
      ArrowLeft: now - SEEK_STEP,
      ArrowRight: now + SEEK_STEP,
      PageDown: now - 5 * SEEK_STEP,
      PageUp: now + 5 * SEEK_STEP,
      Home: 0,
      End: total,
    }
    if (!(event.key in to)) return
    event.preventDefault()
    onSeek(clamp(to[event.key], 0, total))
  }

  return (
    <Panel title="Timeline" className={className}>
      <div className={styles.timeline}>
        {state.cuts.length > 0 && duration !== null && (
          <p className={styles.summary}>
            {formatTime(total)} of source, {formatTime(output)} after cuts
          </p>
        )}
        <div className={`${styles.axis}${onSeek ? ` ${styles.scrub}` : ''}`} aria-hidden="true" {...scrub}>
          {ticks.map((t) => (
            <span key={t} style={{ left: percent(t) }}>
              {formatTime(t)}
            </span>
          ))}
        </div>
        {onTrim && duration !== null && (
          <div className={`${styles.lane} ${styles.trimLane}`}>
            <span className={styles.laneName}>Trim</span>
            <span className={styles.trimTrack}>
              {/* Already trimmed away (undo brings it back), then about to be. */}
              <span className={styles.trimGone} style={{ left: 0, width: percent(keptStart) }} />
              <span
                className={styles.trimGone}
                style={{ left: percent(keptEnd), width: percent(total - keptEnd) }}
              />
              <span
                className={styles.trimShade}
                style={{ left: percent(keptStart), width: percent(trim.start - keptStart) }}
              />
              <span
                className={styles.trimShade}
                style={{ left: percent(trim.end), width: percent(keptEnd - trim.end) }}
              />
              <span
                className={styles.trimKept}
                style={{ left: percent(trim.start), width: percent(trim.end - trim.start) }}
              />
              <input
                type="range"
                aria-label="Trim start"
                className={styles.trimHandle}
                min={0}
                max={total}
                step={TRIM_STEP}
                value={trim.start}
                disabled={trimDisabled}
                onChange={(e) =>
                  setTrim(clamp(Number(e.target.value), keptStart, trim.end - TRIM_STEP), trim.end)
                }
              />
              <input
                type="range"
                aria-label="Trim end"
                className={styles.trimHandle}
                min={0}
                max={total}
                step={TRIM_STEP}
                value={trim.end}
                disabled={trimDisabled}
                onChange={(e) =>
                  setTrim(trim.start, clamp(Number(e.target.value), trim.start + TRIM_STEP, keptEnd))
                }
              />
            </span>
            <span className={styles.trimActions}>
              {alreadyTrimmed && !pending && (
                <span className={styles.trimNote}>
                  Kept {formatTime(keptStart)}–{formatTime(keptEnd)}. Undo brings the rest back.
                </span>
              )}
              <button
                type="button"
                className={styles.trimButton}
                disabled={trimDisabled || !pending}
                onClick={() => {
                  onTrim(trim.start, trim.end)
                  setHandles(null)
                }}
              >
                {pending ? `Trim to ${formatTime(trim.start)}–${formatTime(trim.end)}` : 'Drag to trim'}
              </button>
              {pending && (
                <button type="button" className={styles.trimReset} onClick={() => setHandles(null)}>
                  Reset
                </button>
              )}
            </span>
          </div>
        )}
        {/* Lanes scroll in a capped box, so many mask lanes (one per tracked face) never push
            the player off the screen. The playhead sits inside it, so it spans every lane. */}
        <div className={styles.scroll} data-testid="timeline-scroll">
          <div
            className={`${styles.body}${onSeek ? ` ${styles.scrub}` : ''}`}
            {...scrub}
            {...(onSeek && {
              role: 'slider',
              tabIndex: 0,
              'aria-label': 'Playhead',
              'aria-valuemin': 0,
              'aria-valuemax': Math.round(total * 10) / 10,
              'aria-valuenow': Math.round((time ?? 0) * 10) / 10,
              'aria-valuetext': formatTime(time ?? 0),
              onKeyDown: seekByKey,
            })}
          >
            <ul className={styles.lanes} aria-label="Timeline lanes">
              {lanesOf(state, tracks).map((lane) => (
                <li key={lane.id} className={styles.lane}>
                  <span className={styles.laneName} title={lane.id}>
                    {lane.name}
                  </span>
                  <span className={styles.track}>
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
            <span className={styles.playheadRail} ref={rail} aria-hidden="true">
              {time !== undefined && <span className={styles.playhead} style={{ left: percent(time) }} />}
            </span>
          </div>
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
