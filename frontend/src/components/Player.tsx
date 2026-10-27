import { Panel } from './Panel'
import styles from './Player.module.css'

/** Plays the uploaded file locally. Rendered previews come with the renderer (F-04). */
export function Player({
  src,
  className,
  onDuration,
}: {
  src: string | null
  className?: string
  onDuration: (seconds: number) => void
}) {
  return (
    <Panel title="Player" className={className}>
      {src ? (
        <video
          className={styles.video}
          src={src}
          controls
          onLoadedMetadata={(event) => {
            const { duration } = event.currentTarget
            if (Number.isFinite(duration)) onDuration(duration)
          }}
        />
      ) : (
        <p className={styles.empty}>No video yet. Upload one to start.</p>
      )}
    </Panel>
  )
}
