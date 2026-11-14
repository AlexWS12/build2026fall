import { useEffect, useImperativeHandle, useRef, useState, type CSSProperties, type Ref } from 'react'
import type { TimelineState, Track } from '../api/types'
import { Panel } from './Panel'
import styles from './Player.module.css'
import { cutAt, masksAt, mutedAt, type PreviewMask } from './preview'

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
  state,
  tracks,
  className,
  handle,
  onTime,
  onDuration,
}: {
  src: string | null
  /** Width over height of the picture, when the server knows it. */
  aspect?: number | null
  state?: TimelineState | null
  tracks?: Track[]
  className?: string
  handle?: Ref<PlayerHandle>
  onTime?: (seconds: number) => void
  onDuration: (seconds: number) => void
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
  const ratio = aspect || measuredAspect

  return (
    <Panel title="Player" className={className}>
      {src ? (
        <div className={styles.player}>
          <div className={styles.stage} style={ratio ? { aspectRatio: `${ratio}` } : undefined}>
            <video
              ref={video}
              className={styles.video}
              src={src}
              controls
              onLoadedMetadata={(event) => {
                const { duration, videoWidth, videoHeight } = event.currentTarget
                if (Number.isFinite(duration)) onDuration(duration)
                if (videoWidth && videoHeight) setMeasuredAspect(videoWidth / videoHeight)
              }}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onTimeUpdate={tick}
              onSeeked={tick}
            />
            <div className={styles.overlay} aria-hidden="true">
              {masks.map((mask) => (
                <span
                  key={mask.trackId}
                  className={`${styles.mask} ${styles[mask.effect.type]}`}
                  style={maskStyle(mask)}
                />
              ))}
            </div>
          </div>
          {state && (
            <div className={styles.bar}>
              <div className={styles.modes} role="group" aria-label="What the player shows">
                <button type="button" aria-pressed={mode === 'preview'} onClick={() => setMode('preview')}>
                  Preview edits
                </button>
                <button
                  type="button"
                  aria-pressed={mode === 'original'}
                  onClick={() => setMode('original')}
                >
                  Original
                </button>
              </div>
              <span className={styles.note}>
                {mode === 'preview'
                  ? 'An approximate preview. The export is the exact render.'
                  : 'Showing the untouched source.'}
              </span>
            </div>
          )}
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
