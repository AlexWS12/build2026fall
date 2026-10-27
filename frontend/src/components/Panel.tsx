import { useId, type ReactNode } from 'react'
import styles from './Panel.module.css'

/** A titled region of the shell. Tests find it with `getByRole('region', { name: title })`. */
export function Panel({
  title,
  className,
  children,
}: {
  title: string
  className?: string
  children: ReactNode
}) {
  const headingId = useId()
  return (
    <section
      aria-labelledby={headingId}
      className={className ? `${styles.panel} ${className}` : styles.panel}
    >
      <h2 id={headingId} className={styles.title}>
        {title}
      </h2>
      <div className={styles.body}>{children}</div>
    </section>
  )
}
