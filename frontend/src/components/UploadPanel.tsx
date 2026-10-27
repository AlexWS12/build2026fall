import type { ProjectInfo } from '../api/types'
import { Panel } from './Panel'

export function UploadPanel({
  project,
  busy,
  error,
  onUpload,
}: {
  project: ProjectInfo | null
  busy: boolean
  error: string | null
  onUpload: (file: File) => void
}) {
  return (
    <Panel title="Upload">
      <label>
        Video file{' '}
        <input
          type="file"
          accept="video/*"
          disabled={busy}
          onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) onUpload(file)
          }}
        />
      </label>
      {busy && <p>Uploading…</p>}
      {error && <p role="alert">{error}</p>}
      {project && !busy && (
        <p>
          Project <strong>{project.name}</strong> ({project.video.original_name})
        </p>
      )}
    </Panel>
  )
}
