import styles from './Logo.module.css'

/** The Lanes mark: the timeline's three lane colours, a cut, and the playhead. */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="16" fill="#24212c" />
      <rect x="12" y="15" width="40" height="8" rx="4" fill="#8f81f7" />
      <rect x="12" y="28" width="15" height="8" rx="4" fill="#f2b866" />
      <rect x="35" y="28" width="17" height="8" rx="4" fill="#f2b866" />
      <rect x="12" y="41" width="26" height="8" rx="4" fill="#55bdb4" />
      <rect x="30" y="9" width="2.5" height="46" rx="1.25" fill="#f3f0f8" />
    </svg>
  )
}

export function Logo({ size }: { size?: number }) {
  return (
    <span className={styles.logo}>
      <LogoMark size={size} />
      <span className={styles.word}>vibe</span>
    </span>
  )
}
