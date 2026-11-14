import { useId, type ReactNode } from 'react'
import styles from './Panel.module.css'

/** A titled region of the editor. Tests find it with `getByRole('region', { name: title })`. */
export function Panel({
  title,
  actions,
  className,
  children,
}: {
  title: string
  /** Controls shown at the right of the title bar. */
  actions?: ReactNode
  className?: string
  children: ReactNode
}) {
  const headingId = useId()
  return (
    <section
      aria-labelledby={headingId}
      className={className ? `${styles.panel} ${className}` : styles.panel}
    >
      <div className={styles.bar}>
        <h2 id={headingId} className={styles.title}>
          {title}
        </h2>
        {actions}
      </div>
      <div className={styles.body}>{children}</div>
    </section>
  )
}
