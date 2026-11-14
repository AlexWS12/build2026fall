import { useEffect, useMemo, useRef, useState } from 'react'
import { defaultClient, type ApiClient } from './api'
import type { ProjectInfo } from './api/types'
import styles from './App.module.css'
import { ChatPanel } from './components/ChatPanel'
import { Landing } from './components/Landing'
import { Logo } from './components/Logo'
import { Player, type PlayerHandle } from './components/Player'
import { StatusBadge } from './components/StatusBadge'
import { formatTime } from './components/timeline'
import { TimelineView } from './components/TimelineView'
import { Toolbar } from './components/Toolbar'
import {
  busyLabel,
  canExport,
  canOpen,
  canRedo,
  canSend,
  canUndo,
  openProject,
  requestStatuses,
} from './state/session'
import { useSession } from './state/useSession'

/** The start screen until a project is open, then the editor, around one session (state/session.ts). */
function App({ api = defaultClient }: { api?: ApiClient }) {
  const { session, commands } = useSession(api)
  const project = openProject(session)
  const player = useRef<PlayerHandle>(null)

  // Both belong to one video: readings taken from a previous one are ignored.
  const [measured, setMeasured] = useState<{ url: string; seconds: number } | null>(null)
  const [position, setPosition] = useState<{ url: string; seconds: number } | null>(null)
  const [focusedRequest, setFocusedRequest] = useState<string | null>(null)
  const [recent, setRecent] = useState<ProjectInfo[]>([])

  // Refreshed whenever the start screen comes back, so a project just made is listed.
  const onStartScreen = project === null
  useEffect(() => {
    if (!onStartScreen) return
    let active = true
    api.listProjects().then(
      (projects) => active && setRecent(projects),
      () => {}, // The status badge already says when the backend is down.
    )
    return () => {
      active = false
    }
  }, [api, onStartScreen])

  const undone = useMemo(() => {
    const ids = new Set<string>()
    if (project) {
      for (const [id, status] of requestStatuses(project.history)) {
        if (status === 'undone') ids.add(id)
      }
    }
    return ids
  }, [project])

  const opening = session.status === 'opening' ? session.label : null
  const error = session.status === 'opening' ? null : session.error

  if (!project) {
    return (
      <Landing
        status={<StatusBadge api={api} />}
        busy={opening}
        error={error}
        recent={recent}
        onUpload={commands.upload}
        onOpen={commands.open}
      />
    )
  }

  const { videoUrl } = project
  const measuredDuration = measured && measured.url === videoUrl ? measured.seconds : null
  const time = position && position.url === videoUrl ? position.seconds : 0
  const { video, info } = project.detail
  const duration = video.duration || measuredDuration
  const activity = session.status === 'open' ? busyLabel(session) : null
  const targets = project.tracks.length

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <button
          type="button"
          className={styles.home}
          aria-label="Back to the start screen"
          disabled={!canOpen(session)}
          onClick={commands.close}
        >
          <Logo size={26} />
        </button>
        <span className={styles.rule} aria-hidden="true" />
        <strong className={styles.name}>{info.name}</strong>
        {video.duration > 0 && (
          <span className={styles.meta}>
            {formatTime(video.duration)} · {video.width}×{video.height}
          </span>
        )}
        <div className={styles.headerRight}>
          {error && (
            <span className={styles.error} role="alert">
              {error}
            </span>
          )}
          <StatusBadge api={api} />
          {project.analysed ? (
            <span className={styles.pill}>
              Analysed · {targets} {targets === 1 ? 'target' : 'targets'}
            </span>
          ) : (
            <button
              type="button"
              className={styles.pill}
              title="Find people, faces and plates, so they can be blurred"
              disabled={!canSend(session)}
              onClick={commands.analyse}
            >
              Not analysed · Analyse video
            </button>
          )}
          <Toolbar
            canUndo={canUndo(session)}
            canRedo={canRedo(session)}
            canExport={canExport(session)}
            exporting={project.activity.kind === 'exporting'}
            exported={project.export}
            onUndo={commands.undo}
            onRedo={commands.redo}
            onExport={commands.exportVideo}
          />
        </div>
      </header>
      <main className={styles.grid}>
        <Player
          // A new video is a new player: its position and mute state start over.
          key={videoUrl || 'none'}
          src={videoUrl || null}
          aspect={video.height > 0 && video.duration > 0 ? video.width / video.height : null}
          duration={duration}
          state={project.timeline}
          tracks={project.tracks}
          className={styles.player}
          handle={player}
          onTime={(seconds) => setPosition({ url: videoUrl, seconds })}
          onDuration={(seconds) => setMeasured({ url: videoUrl, seconds })}
        />
        <ChatPanel
          className={styles.chat}
          entries={project.chat}
          disabled={false}
          busy={canSend(session) ? null : (activity ?? opening)}
          undone={undone}
          focusedRequest={focusedRequest}
          onSend={commands.send}
          onFocusRequest={setFocusedRequest}
        />
        <TimelineView
          state={project.timeline}
          duration={duration ?? null}
          tracks={project.tracks}
          time={time}
          focusedRequest={focusedRequest}
          className={styles.timeline}
          onSeek={(seconds) => player.current?.seek(seconds)}
          onFocusRequest={setFocusedRequest}
        />
      </main>
    </div>
  )
}

export default App
