import { useState, type DragEvent, type ReactNode } from 'react'
import type { ProjectInfo } from '../api/types'
import styles from './Landing.module.css'
import { Logo } from './Logo'

const REQUESTS: { kind: 'mask' | 'cut' | 'mute' | 'when' | 'ask' | 'undo'; label: string; say: string; does: string }[] = [
  {
    kind: 'mask',
    label: 'Hide',
    say: '"blur faces for the first half"',
    does: 'Blur, pixelate or black out faces, people and license plates, for the whole clip or part of it.',
  },
  {
    kind: 'cut',
    label: 'Cut',
    say: '"keep only 0:30 to 1:00"',
    does: 'Cut a range out, or keep one and drop the rest. Other edits never shift, because every time refers to the original clip.',
  },
  {
    kind: 'mute',
    label: 'Mute',
    say: '"mute the first 5 seconds"',
    does: 'Silence a stretch with soft edges, so it never clicks. Say "unmute" to bring part of it back.',
  },
  {
    kind: 'when',
    label: 'When',
    say: '"the last third", "after 0:30"',
    does: 'Halves, thirds, first or last N seconds, before, after and between. A time past the end is refused, never clamped.',
  },
  {
    kind: 'ask',
    label: 'It asks back',
    say: '"cut it"',
    does: "Gets a question, not a guess: which part? Anything it can't read, it asks about.",
  },
  {
    kind: 'undo',
    label: 'Take it back',
    say: '"undo"',
    does: 'One request is one step. Undo removes everything it did, and redo brings it back.',
  },
]

const STEPS = [
  ['You say it', 'A chat message in your own words.'],
  ['The agent writes a plan', 'One plan of tool calls, checked before anything runs. Its only power is calling tools.'],
  ['Tools write the timeline', 'Typed, validated edits in an append-only log. Each one remembers the request that made it.'],
  ['The renderer exports', 'Same timeline, same source, same output. Every time.'],
]

/** The start screen: what the app is, and the one thing to do next, which is drop a video. */
export function Landing({
  status,
  busy,
  error,
  recent,
  onUpload,
  onOpen,
}: {
  /** The backend status badge. */
  status: ReactNode
  /** What's loading ("Uploading clip.mp4…"), or null. */
  busy: string | null
  error: string | null
  /** Projects already on the server. */
  recent: ProjectInfo[]
  onUpload: (file: File) => void
  onOpen: (projectId: string) => void
}) {
  const [over, setOver] = useState(false)

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setOver(false)
    const file = event.dataTransfer.files?.[0]
    if (file && !busy) onUpload(file)
  }

  return (
    <div className={styles.page}>
      <header className={styles.nav}>
        <Logo size={30} />
        <nav aria-label="On this page">
          <a href="#say">What you can say</a>
          <a href="#how">How it works</a>
          <a href="#live">Live mode</a>
        </nav>
        {status}
      </header>

      <main>
        <section className={styles.hero}>
          <div className={styles.pitch}>
            <span className={styles.eyebrow}>Chat-driven video editor</span>
            <h1>
              Say the edit.
              <br />
              Watch it land on the timeline.
            </h1>
            <p className={styles.lede}>
              Load a clip and type what you want changed: blur the faces, cut the first ten seconds, mute
              a stretch. An agent plans it, typed tools write it to a timeline you can read and undo, and a
              renderer exports the result.
            </p>
            <label
              className={`${styles.drop}${over ? ` ${styles.over}` : ''}`}
              onDragOver={(event) => {
                event.preventDefault()
                setOver(true)
              }}
              onDragLeave={() => setOver(false)}
              onDrop={onDrop}
            >
              <span className={styles.dropIcon} aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" x2="12" y1="3" y2="15" />
                </svg>
              </span>
              <span className={styles.dropText}>
                <strong>{busy ?? 'Drop a video to start'}</strong>
                <span>or browse files. MP4, MOV or WebM, up to 2 GB.</span>
              </span>
              <span className={styles.dropButton} aria-hidden="true">
                Choose a video
              </span>
              <input
                type="file"
                accept="video/*"
                aria-label="Video file"
                disabled={!!busy}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) onUpload(file)
                }}
              />
            </label>
            {error && (
              <p className={styles.error} role="alert">
                {error}
              </p>
            )}
            {recent.length > 0 && !busy && (
              <div className={styles.recent}>
                <span>Or pick up where you left off:</span>
                <ul>
                  {recent.slice(0, 5).map((info) => (
                    <li key={info.project_id}>
                      <button type="button" onClick={() => onOpen(info.project_id)}>
                        {info.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <Vignette />
        </section>

        <section id="say" className={styles.band}>
          <div className={styles.inner}>
            <h2>What you can say</h2>
            <p className={styles.sub}>
              Plain requests are understood on the spot. Anything it can't read, it asks about instead of
              guessing.
            </p>
            <ul className={styles.cards}>
              {REQUESTS.map((request) => (
                <li key={request.label} className={styles[request.kind]}>
                  <span className={styles.cardLabel}>{request.label}</span>
                  <span className={styles.say}>{request.say}</span>
                  <span className={styles.does}>{request.does}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section id="how" className={`${styles.band} ${styles.raised}`}>
          <div className={styles.inner}>
            <h2>The agent never touches a pixel</h2>
            <ol className={styles.steps}>
              {STEPS.map(([title, text], index) => (
                <li key={title}>
                  <span className={styles.number}>0{index + 1}</span>
                  <strong>{title}</strong>
                  <span>{text}</span>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className={styles.band}>
          <div className={`${styles.inner} ${styles.split}`}>
            <div>
              <h2>When it isn't sure, it says so</h2>
              <p className={styles.sub}>
                Models make mistakes. The fallback is you, made quick: a plain report of what was done, the
                tool calls behind it, and one click to take any of it back.
              </p>
            </div>
            <ul className={styles.rules}>
              <li className={styles.mask}>
                <strong>Privacy edits fail closed</strong>
                <span>If it can't tell where a face is, nothing is claimed as hidden, and it says why.</span>
              </li>
              <li className={styles.cut}>
                <strong>Cuts ask first</strong>
                <span>"Cut it" gets a question back, not a guess at which part.</span>
              </li>
              <li className={styles.mute}>
                <strong>Nothing is claimed that didn't happen</strong>
                <span>If an edit can't be applied, the reply says why, and the timeline stays as it was.</span>
              </li>
            </ul>
          </div>
        </section>

        <section id="live" className={styles.band}>
          <div className={`${styles.inner} ${styles.live}`}>
            <div>
              <span className={styles.eyebrow}>Stretch goal, in progress</span>
              <h2>Next: the same rules, live</h2>
              <p className={styles.sub}>
                A standing rule instead of a one-off edit, applied to every frame of a stream. Anyone it
                can't identify stays masked.
              </p>
            </div>
            <p className={styles.quote}>"make sure only I am visible, blur everyone else"</p>
          </div>
        </section>
      </main>

      <footer className={styles.footer}>
        <span>Vibe Editing Agent, working title. Fall 2026 build.</span>
        <span>Built by a team of ten.</span>
      </footer>
    </div>
  )
}

/** A still of the editor, drawn in CSS: decoration, so it's hidden from assistive tech. */
function Vignette() {
  return (
    <div className={styles.vignette} aria-hidden="true">
      <div className={styles.frame}>
        <span className={styles.floor} />
        <span className={`${styles.body} ${styles.p1}`} />
        <span className={`${styles.head} ${styles.h1}`} />
        <span className={`${styles.body} ${styles.p2}`} />
        <span className={`${styles.head} ${styles.h2}`} />
        <span className={`${styles.body} ${styles.p3}`} />
        <span className={`${styles.head} ${styles.h3}`} />
        <span className={`${styles.blurBox} ${styles.b1}`} />
        <span className={`${styles.blurBox} ${styles.b2}`} />
        <span className={`${styles.blurBox} ${styles.b3}`} />
        <span className={styles.chip}>Preview</span>
        <span className={`${styles.chip} ${styles.time}`}>0:31 / 1:32</span>
      </div>
      <div className={styles.bubbleUser}>blur faces for the first half</div>
      <div className={styles.receipt}>
        <span className={styles.receiptLine}>
          <svg width="17" height="17" viewBox="0 0 24 24">
            <path d="M20 6 9 17l-5-5" />
          </svg>
          Blurred 3 faces (0:00–0:46).
        </span>
        <span className={styles.tools}>
          <code>find_objects</code>
          <code>resolve_time</code>
          <code>add_mask</code>
        </span>
      </div>
      <div className={styles.mini}>
        <span>Faces</span>
        <i>
          <b className={styles.miniMask} />
        </i>
        <span>Cuts</span>
        <i>
          <b className={styles.miniCut} />
        </i>
        <span>Mutes</span>
        <i>
          <b className={styles.miniMute} />
        </i>
      </div>
    </div>
  )
}
