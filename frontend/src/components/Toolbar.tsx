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
      <button
        type="button"
        className={styles.icon}
        aria-label="Undo"
        title="Undo the last request"
        disabled={!canUndo}
        onClick={onUndo}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 14 4 9l5-5" />
          <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
        </svg>
      </button>
      <button
        type="button"
        className={styles.icon}
        aria-label="Redo"
        title="Redo"
        disabled={!canRedo}
        onClick={onRedo}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
          <path d="m15 14 5-5-5-5" />
          <path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13" />
        </svg>
      </button>
      <button type="button" className={styles.primary} disabled={!canExport} onClick={onExport}>
        <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" x2="12" y1="15" y2="3" />
        </svg>
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
