import { useState, type CSSProperties, type PointerEvent } from 'react'
import type { Box, Track } from '../api/types'
import { CLICK_SLOP, targetsAt, type EffectType } from './targets'
import { trackNames } from './timeline'
import styles from './TargetLayer.module.css'

type Point = [number, number]

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/**
 * Click or draw a box to target (F-14). Covers the picture while target mode is on: outlines
 * show what is already followed at this moment, a click picks one of them, and a drag draws a
 * box around anything else. Coordinates go out normalized (0..1), like the contract's boxes.
 */
export function TargetLayer({
  tracks,
  time,
  disabled = false,
  onPoint,
  onBox,
}: {
  tracks: Track[]
  /** Source time of the frame on screen. */
  time: number
  /** Something else is in flight: show the targets but take no clicks. */
  disabled?: boolean
  onPoint: (point: Point) => void
  onBox: (box: Box) => void
}) {
  const [drag, setDrag] = useState<{ from: Point; to: Point } | null>(null)
  const names = trackNames(tracks)
  const seen = targetsAt(tracks, time)

  function at(event: PointerEvent<HTMLDivElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect()
    return [
      clamp01((event.clientX - rect.left) / (rect.width || 1)),
      clamp01((event.clientY - rect.top) / (rect.height || 1)),
    ]
  }

  function down(event: PointerEvent<HTMLDivElement>) {
    if (disabled || event.button !== 0) return
    event.currentTarget.setPointerCapture?.(event.pointerId)
    const point = at(event)
    setDrag({ from: point, to: point })
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    if (drag) setDrag({ ...drag, to: at(event) })
  }

  function up(event: PointerEvent<HTMLDivElement>) {
    if (!drag) return
    const to = at(event)
    setDrag(null)
    const [x0, y0] = drag.from
    const [x1, y1] = to
    if (Math.abs(x1 - x0) < CLICK_SLOP && Math.abs(y1 - y0) < CLICK_SLOP) {
      onPoint(drag.from)
      return
    }
    // A box needs some height and width; a flat drag along one edge is taken as a click.
    if (Math.abs(x1 - x0) < CLICK_SLOP || Math.abs(y1 - y0) < CLICK_SLOP) {
      onPoint(drag.from)
      return
    }
    onBox([Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)])
  }

  const drawn = drag && boxOf(drag.from, drag.to)

  return (
    <div
      className={`${styles.layer}${disabled ? ` ${styles.disabled}` : ''}`}
      role="application"
      aria-label="Click a target, or drag a box around something to mask it"
      aria-disabled={disabled || undefined}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={() => setDrag(null)}
    >
      {seen.map(({ track, box }) => (
        <span
          key={track.track_id}
          className={`${styles.outline}${track.source === 'user' ? ` ${styles.drawn}` : ''}`}
          style={place(box)}
          data-track={track.track_id}
        >
          <span className={styles.name}>{names.get(track.track_id) ?? track.label}</span>
        </span>
      ))}
      {drawn && <span className={styles.drawing} style={place(drawn)} data-testid="drawing" />}
    </div>
  )
}

function boxOf(a: Point, b: Point): Box {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]
}

function place([x1, y1, x2, y2]: Box): CSSProperties {
  return {
    left: `${x1 * 100}%`,
    top: `${y1 * 100}%`,
    width: `${(x2 - x1) * 100}%`,
    height: `${(y2 - y1) * 100}%`,
  }
}

const EFFECTS: { type: EffectType; label: string }[] = [
  { type: 'blur', label: 'Blur' },
  { type: 'pixelate', label: 'Pixelate' },
  { type: 'box', label: 'Black box' },
]

/** Under the picture while target mode is on: what to do, which effect, and the way out. */
export function TargetBar({
  effect,
  busy,
  onEffect,
  onDone,
}: {
  effect: EffectType
  /** The server is following a box or doing something else. */
  busy: boolean
  onEffect: (effect: EffectType) => void
  onDone: () => void
}) {
  return (
    <div className={styles.bar} role="group" aria-label="Target by hand">
      <div className={styles.effects} role="group" aria-label="Effect">
        {EFFECTS.map(({ type, label }) => (
          <button
            key={type}
            type="button"
            aria-pressed={effect === type}
            onClick={() => onEffect(type)}
          >
            {label}
          </button>
        ))}
      </div>
      <span className={styles.hint} aria-live="polite">
        {busy
          ? 'Working on it…'
          : 'Click an outlined target, or drag a box around anything else. It follows the box through the clip.'}
      </span>
      <button type="button" className={styles.done} onClick={onDone}>
        Done
      </button>
    </div>
  )
}
