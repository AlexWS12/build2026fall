import type { Transcript } from '../api/types'
import { Panel } from './Panel'
import styles from './TranscriptPanel.module.css'

/** "0:07.08": minutes, then seconds to the hundredth. Rounds first, so 59.997 is "1:00.00". */
const clock = (seconds: number) => {
  const cs = Math.round(seconds * 100)
  return `${Math.floor(cs / 6000)}:${((cs % 6000) / 100).toFixed(2).padStart(5, '0')}`
}

/** The video's transcript (F-31): made on request, then used by the agent for "where he says X". */
export function TranscriptPanel({
  transcript,
  disabled,
  busy,
  error,
  onTranscribe,
}: {
  transcript: Transcript | null
  /** True until there's a project, or while a chat request is running. */
  disabled: boolean
  busy: boolean
  error: string | null
  onTranscribe: () => void
}) {
  return (
    <Panel title="Transcript">
      <button type="button" disabled={disabled || busy} onClick={onTranscribe}>
        Transcribe
      </button>
      {busy && <p>Transcribing…</p>}
      {error && <p role="alert">{error}</p>}
      {transcript && transcript.segments.length === 0 && <p>No speech found.</p>}
      {transcript && transcript.segments.length > 0 && (
        <ol className={styles.segments} aria-label="Transcript segments">
          {transcript.segments.map((segment, i) => (
            <li key={i}>
              <span className={styles.time}>{clock(segment.start)}</span> {segment.text}
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
