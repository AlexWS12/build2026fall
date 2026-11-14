import type { ExportState } from '../state/session'
import styles from './Toolbar.module.css'
import { formatTime } from './timeline'

/** Undo, redo and export for the open project, and what the last export produced. */
export function Toolbar({
  canUndo,
  canRedo,
  canExport,
  exporting,
  exported,
  onUndo,
  onRedo,
  onExport,
}: {
  canUndo: boolean
  canRedo: boolean
  canExport: boolean
  exporting: boolean
  exported: ExportState
  onUndo: () => void
  onRedo: () => void
  onExport: () => void
}) {
  return (
    <div className={styles.toolbar} role="toolbar" aria-label="Project actions">
      <ExportResult exported={exported} />
      <button type="button" disabled={!canUndo} onClick={onUndo}>
        Undo
      </button>
      <button type="button" disabled={!canRedo} onClick={onRedo}>
        Redo
      </button>
      <button type="button" className={styles.primary} disabled={!canExport} onClick={onExport}>
        {exporting ? 'Rendering…' : 'Export'}
      </button>
    </div>
  )
}

function ExportResult({ exported }: { exported: ExportState }) {
  if (exported.kind === 'none') return null
  if (exported.kind === 'failed') {
    return (
      <span className={styles.failed} role="alert">
        Export failed: {exported.message}
      </span>
    )
  }
  const { result, stale } = exported
  return (
    <span className={styles.result}>
      <a href={result.url} download={result.name}>
        Download export ({formatTime(result.duration)})
      </a>
      {stale && <span className={styles.stale}>The timeline changed since.</span>}
      {result.issues.length > 0 && (
        <details>
          <summary>
            {result.issues.length} {result.issues.length === 1 ? 'spot' : 'spots'} to check
          </summary>
          <ul>
            {result.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </details>
      )}
    </span>
  )
}
