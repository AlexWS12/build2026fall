import { useEffect, useState } from 'react'
import type { ApiClient } from '../api'
import styles from './StatusBadge.module.css'

type Status = { kind: 'checking' } | { kind: 'ok'; text: string } | { kind: 'down' }

/** Shows whether the backend answers `/api/health`, and which model backend it runs. */
export function StatusBadge({ api }: { api: ApiClient }) {
  const [status, setStatus] = useState<Status>({ kind: 'checking' })

  useEffect(() => {
    let active = true
    api.health().then(
      (health) => active && setStatus({ kind: 'ok', text: `${health.status} (${health.backend})` }),
      () => active && setStatus({ kind: 'down' }),
    )
    return () => {
      active = false
    }
  }, [api])

  const text =
    status.kind === 'checking'
      ? 'Backend: checking…'
      : status.kind === 'ok'
        ? `Backend: ${status.text}`
        : 'Backend: unreachable'

  return (
    <span
      role="status"
      className={status.kind === 'checking' ? styles.badge : `${styles.badge} ${styles[status.kind]}`}
    >
      {text}
    </span>
  )
}
