import { useEffect, useMemo, useRef, useState } from 'react'
import { defaultClient, type ApiClient } from './api'
import type { ProjectInfo } from './api/types'
import styles from './App.module.css'
import { ChatPanel } from './components/ChatPanel'
import { Player, type PlayerHandle } from './components/Player'
import { StatusBadge } from './components/StatusBadge'
import { TimelineView } from './components/TimelineView'
import { Toolbar } from './components/Toolbar'
import { UploadPanel } from './components/UploadPanel'
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

const NO_ENTRIES: never[] = []

/** The editor: upload, player, chat and timeline around one session (state/session.ts). */
function App({ api = defaultClient }: { api?: ApiClient }) {
  const { session, commands } = useSession(api)
  const project = openProject(session)
  const player = useRef<PlayerHandle>(null)

  // Both belong to one video: readings taken from a previous one are ignored.
  const [measured, setMeasured] = useState<{ url: string; seconds: number } | null>(null)
  const [position, setPosition] = useState<{ url: string; seconds: number } | null>(null)
  const [focusedRequest, setFocusedRequest] = useState<string | null>(null)
  const [recent, setRecent] = useState<ProjectInfo[]>([])

  useEffect(() => {
    let active = true
    api.listProjects().then(
      (projects) => active && setRecent(projects),
      () => {}, // The status badge already says when the backend is down.
    )
    return () => {
      active = false
    }
  }, [api])

  const videoUrl = project?.videoUrl ?? null
  const measuredDuration = measured && measured.url === videoUrl ? measured.seconds : null
  const time = position && position.url === videoUrl ? position.seconds : 0

  const video = project?.detail.video
  const duration = video?.duration || measuredDuration
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
  const activity = session.status === 'open' ? busyLabel(session) : null
  const error = session.status === 'opening' ? null : session.error

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <h1 className={styles.title}>Vibe Editing Agent</h1>
        <div className={styles.headerRight}>
          {project && (
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
          )}
          <StatusBadge api={api} />
        </div>
      </header>
      <main className={styles.grid}>
        <Player
          // A new video is a new player: its position and mute state start over.
          key={videoUrl ?? 'none'}
          src={videoUrl || null}
          aspect={video && video.height > 0 && video.duration > 0 ? video.width / video.height : null}
          state={project?.timeline ?? null}
          tracks={project?.tracks}
          className={styles.player}
          handle={player}
          onTime={(seconds) => videoUrl && setPosition({ url: videoUrl, seconds })}
          onDuration={(seconds) => videoUrl && setMeasured({ url: videoUrl, seconds })}
        />
        <div className={styles.side}>
          <UploadPanel
            project={project?.detail.info ?? null}
            busy={opening}
            disabled={!canOpen(session)}
            error={error}
            recent={recent}
            analysed={project?.analysed}
            onUpload={commands.upload}
            onOpen={commands.open}
            onAnalyse={commands.analyse}
          />
          <ChatPanel
            className={styles.chat}
            entries={project?.chat ?? NO_ENTRIES}
            disabled={!project}
            busy={project && !canSend(session) ? (activity ?? opening) : null}
            undone={undone}
            focusedRequest={focusedRequest}
            onSend={commands.send}
            onFocusRequest={setFocusedRequest}
          />
        </div>
        <TimelineView
          state={project?.timeline ?? null}
          duration={duration ?? null}
          tracks={project?.tracks}
          time={project ? time : undefined}
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
