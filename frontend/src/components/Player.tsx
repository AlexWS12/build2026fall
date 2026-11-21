import { useEffect, useImperativeHandle, useRef, useState, type CSSProperties, type Ref } from 'react'
import type { TimelineState, Track } from '../api/types'
import { Panel } from './Panel'
import styles from './Player.module.css'
import { cutAt, masksAt, mutedAt, outputDuration, type PreviewMask } from './preview'
import { formatTime, trackNames } from './timeline'

export interface PlayerHandle {
  /** Move the playhead to a source time, in seconds. */
  seek(seconds: number): void
}

type Mode = 'preview' | 'original'

/**
 * Plays the source video. In preview mode it shows the timeline's edits as it plays: masks are
 * drawn over the picture, cut ranges are skipped and muted ranges are silent. That preview is
 * made in the browser and is approximate; the export is the exact render.
 */
export function Player({
  src,
  aspect,
  duration,
  state,
  tracks,
  className,
  handle,
  onTime,
  onDuration,
  onRemoveMasks,
  editable = false,
}: {
  src: string | null
  /** Width over height of the picture, when the server knows it. */
  aspect?: number | null
  /** Seconds of source video, once known. */
  duration?: number | null
  state?: TimelineState | null
  tracks?: Track[]
  className?: string
  handle?: Ref<PlayerHandle>
  onTime?: (seconds: number) => void
  onDuration: (seconds: number) => void
  /** Hand edit (F-42): remove a mask the viewer clicked. Without it, masks aren't clickable. */
  onRemoveMasks?: (trackIds: string[], label: string) => void
  /** Whether a hand edit can start now (nothing else in flight). */
  editable?: boolean
}) {
  const video = useRef<HTMLVideoElement>(null)
  const [mode, setMode] = useState<Mode>('preview')
  const [time, setTime] = useState(0)
  const [measuredAspect, setMeasuredAspect] = useState<number | null>(null)
  /** True while the preview, not the viewer, is holding the sound off. */
  const forcedMute = useRef(false)

  const previewing = mode === 'preview' && !!state

  useImperativeHandle(handle, () => ({
    seek(seconds) {
      if (video.current) video.current.currentTime = seconds
    },
  }))

  function tick() {
    const element = video.current
    if (!element) return
    let t = element.currentTime
    if (previewing) {
      const cut = cutAt(state, t)
      if (cut) {
        if (cut.end === null || cut.end >= element.duration) {
          // Cut to the end: the edited video stops here.
          element.pause()
          t = cut.start
        } else {
          t = cut.end
        }
        element.currentTime = t
      }
      const silent = mutedAt(state, t)
      if (silent && !element.muted) {
        element.muted = true
        forcedMute.current = true
      } else if (!silent && forcedMute.current) {
        element.muted = false
        forcedMute.current = false
      }
    } else if (forcedMute.current) {
      element.muted = false
      forcedMute.current = false
    }
    setTime(t)
    onTime?.(t)
  }

  // Follow playback every frame, so masks stay on their targets between `timeupdate` events.
  const tickRef = useRef(tick)
  useEffect(() => {
    tickRef.current = tick
  })
  const [playing, setPlaying] = useState(false)
  useEffect(() => {
    if (!playing || typeof requestAnimationFrame !== 'function') return
    let frame = requestAnimationFrame(function loop() {
      tickRef.current()
      frame = requestAnimationFrame(loop)
    })
    return () => cancelAnimationFrame(frame)
  }, [playing])

  // Switching mode, or an edit landing, takes effect on the frame being shown.
  useEffect(() => {
    tickRef.current()
  }, [mode, state])

  const masks = previewing ? masksAt(state, tracks ?? [], time) : []
  const names = trackNames(tracks ?? [])
  const nameOf = (trackId: string) => names.get(trackId) ?? trackId
  const [picked, setPicked] = useState<string | null>(null)
  // A selection only lasts while its mask is still shown (undo, another edit, or time moving on
  // can take it away), so Remove never acts on something the viewer can't see.
  const selected = picked && masks.some((m) => m.trackId === picked) ? picked : null
  const clickable = !!onRemoveMasks && previewing

  function pick(event: React.MouseEvent<HTMLElement>) {
    // The smallest mask under the click: in a crowd a face sits inside a bigger head box.
    const stage = event.currentTarget.parentElement?.getBoundingClientRect()
    if (!stage) return
    const x = (event.clientX - stage.left) / stage.width
    const y = (event.clientY - stage.top) / stage.height
    const hit = masks
      .filter(({ box }) => !box || (x >= box[0] && x <= box[2] && y >= box[1] && y <= box[3]))
      .sort((a, b) => area(a) - area(b))[0]
    setPicked(hit && hit.trackId !== selected ? hit.trackId : null)
  }

  function remove() {
    if (!selected || !onRemoveMasks || !editable) return
    onRemoveMasks([selected], nameOf(selected))
    setPicked(null)
  }

  const removeRef = useRef(remove)
  useEffect(() => {
    removeRef.current = remove
  })
  useEffect(() => {
    if (!selected) return
    function onKey(event: KeyboardEvent) {
      // Typing in the chat must never delete a mask. The target can be the window itself.
      const target = event.target
      if (target instanceof Element && target.closest('input, textarea, [contenteditable="true"]'))
        return
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault()
        removeRef.current()
      } else if (event.key === 'Escape') {
        setPicked(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected])
  const ratio = aspect || measuredAspect
  const output = state && duration ? outputDuration(state, duration) : null

  const modes = src && state && (
    <div className={styles.modes} role="group" aria-label="What the player shows">
      <button type="button" aria-pressed={mode === 'preview'} onClick={() => setMode('preview')}>
        Preview
      </button>
      <button type="button" aria-pressed={mode === 'original'} onClick={() => setMode('original')}>
        Original
      </button>
    </div>
  )

  return (
    <Panel title="Player" actions={modes} className={className}>
      {src ? (
        <div className={styles.player}>
          <div className={styles.well}>
            <div className={styles.stage} style={{ '--ratio': ratio ?? 16 / 9 } as CSSProperties}>
              <video
                ref={video}
                className={styles.video}
                src={src}
                controls
                onLoadedMetadata={(event) => {
                  const { duration: seconds, videoWidth, videoHeight } = event.currentTarget
                  if (Number.isFinite(seconds)) onDuration(seconds)
                  if (videoWidth && videoHeight) setMeasuredAspect(videoWidth / videoHeight)
                }}
                onPlay={() => setPlaying(true)}
                onPause={() => setPlaying(false)}
                onEnded={() => setPlaying(false)}
                onTimeUpdate={tick}
                onSeeked={tick}
              />
              <div className={styles.overlay} aria-hidden={clickable ? undefined : true}>
                {masks.map((mask) =>
                  clickable ? (
                    <button
                      type="button"
                      key={mask.trackId}
                      className={`${styles.mask} ${styles[mask.effect.type]} ${styles.pickable}${
                        mask.trackId === selected ? ` ${styles.selected}` : ''
                      }`}
                      style={maskStyle(mask)}
                      aria-label={`Select the mask on ${nameOf(mask.trackId)}`}
                      aria-pressed={mask.trackId === selected}
                      onClick={pick}
                    />
                  ) : (
                    <span
                      key={mask.trackId}
                      className={`${styles.mask} ${styles[mask.effect.type]}`}
                      style={maskStyle(mask)}
                    />
                  ),
                )}
              </div>
            </div>
          </div>
          {selected && (
            <div className={styles.selection} role="group" aria-label="Selected mask">
              <span>
                <strong>{nameOf(selected)}</strong> selected
              </span>
              <button type="button" onClick={remove} disabled={!editable}>
                Remove blur
              </button>
              <button type="button" className={styles.quiet} onClick={() => setPicked(null)}>
                Cancel
              </button>
              <span className={styles.hint}>Delete removes it, Esc cancels. Undo brings it back.</span>
            </div>
          )}
          <div className={styles.bar}>
            <span className={styles.clock}>
              {formatTime(time)}
              {duration ? <span> / {formatTime(duration)}</span> : null}
            </span>
            {output !== null && duration && output < duration && (
              <span className={styles.note}>
                Exports as <span className={styles.clock}>{formatTime(output)}</span> after cuts
              </span>
            )}
            {state && (
              <span className={styles.note}>
                {mode === 'preview'
                  ? 'An approximate preview. The export is the exact render.'
                  : 'Showing the untouched source.'}
              </span>
            )}
          </div>
        </div>
      ) : (
        <p className={styles.empty}>No video yet. Upload one to start.</p>
      )}
    </Panel>
  )
}

function maskStyle({ box }: PreviewMask): CSSProperties {
  if (!box) return { inset: 0 }
  const [x1, y1, x2, y2] = box
  return {
    left: `${x1 * 100}%`,
    top: `${y1 * 100}%`,
    width: `${(x2 - x1) * 100}%`,
    height: `${(y2 - y1) * 100}%`,
  }
}

const area = ({ box }: PreviewMask): number => (box ? (box[2] - box[0]) * (box[3] - box[1]) : 1)
