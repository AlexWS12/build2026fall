import type { ProjectInfo } from '../api/types'
import { Panel } from './Panel'
import styles from './UploadPanel.module.css'

export function UploadPanel({
  project,
  busy,
  disabled,
  error,
  recent,
  analysed,
  onUpload,
  onOpen,
  onAnalyse,
}: {
  project: ProjectInfo | null
  /** What's loading, as one line ("Uploading clip.mp4…"), or null. */
  busy: string | null
  /** True while a new project can't be opened (the current one is busy). */
  disabled?: boolean
  error: string | null
  /** Projects already on the server, offered while none is open. */
  recent?: ProjectInfo[]
  /** Whether the open project's video has been analysed (masks need it). */
  analysed?: boolean
  onUpload: (file: File) => void
  onOpen?: (projectId: string) => void
  onAnalyse?: () => void
}) {
  return (
    <Panel title="Upload">
      <label>
        Video file{' '}
        <input
          type="file"
          accept="video/*"
          disabled={!!busy || disabled}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) onUpload(file)
          }}
        />
      </label>
      {busy && <p>{busy}</p>}
      {error && <p role="alert">{error}</p>}
      {project && !busy && (
        <p>
          Project <strong>{project.name}</strong> ({project.video.original_name})
        </p>
      )}
      {project && !busy && analysed === false && (
        <p className={styles.hint}>
          Not analysed yet, so there's nothing to blur.{' '}
          <button type="button" disabled={disabled} onClick={onAnalyse}>
            Analyse video
          </button>
        </p>
      )}
      {!project && !busy && recent && recent.length > 0 && (
        <div className={styles.recent}>
          <span>Or reopen a project:</span>
          <ul>
            {recent.slice(0, 5).map((info) => (
              <li key={info.project_id}>
                <button type="button" onClick={() => onOpen?.(info.project_id)}>
                  {info.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  )
}
