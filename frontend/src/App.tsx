import { useEffect, useState } from 'react'
import { defaultClient, type ApiClient } from './api'
import type { ProjectInfo, TimelineState } from './api/types'
import styles from './App.module.css'
import { ChatPanel, type ChatMessage } from './components/ChatPanel'
import { Player } from './components/Player'
import { StatusBadge } from './components/StatusBadge'
import { TimelineView } from './components/TimelineView'
import { UploadPanel } from './components/UploadPanel'

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error))

/** The F-07 shell: upload, player, chat and timeline, wired to an ApiClient (mock by default). */
function App({ api = defaultClient }: { api?: ApiClient }) {
  const [project, setProject] = useState<ProjectInfo | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [videoUrl, setVideoUrl] = useState<string | null>(null)
  const [duration, setDuration] = useState<number | null>(null)
  const [timeline, setTimeline] = useState<TimelineState | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [thinking, setThinking] = useState(false)

  // The player plays the local file; free its object URL when it's replaced.
  useEffect(() => {
    if (!videoUrl) return
    return () => URL.revokeObjectURL(videoUrl)
  }, [videoUrl])

  const say = (from: ChatMessage['from'], text: string) =>
    setMessages((previous) => [...previous, { id: `${previous.length}`, from, text }])

  async function handleUpload(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const created = await api.createProject(file)
      setProject(created)
      setVideoUrl(URL.createObjectURL(file))
      setDuration(null)
      setMessages([])
      setTimeline(await api.getState(created.project_id))
    } catch (error) {
      setUploadError(`Upload failed: ${errorText(error)}`)
    } finally {
      setUploading(false)
    }
  }

  async function handleSend(text: string) {
    if (!project) return
    say('user', text)
    setThinking(true)
    try {
      const { reply } = await api.sendRequest(project.project_id, text)
      say('agent', reply)
      setTimeline(await api.getState(project.project_id))
    } catch (error) {
      say('agent', `Request failed: ${errorText(error)}`)
    } finally {
      setThinking(false)
    }
  }

  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <h1 className={styles.title}>Vibe Editing Agent</h1>
        <StatusBadge api={api} />
      </header>
      <main className={styles.grid}>
        <Player src={videoUrl} className={styles.player} onDuration={setDuration} />
        <div className={styles.side}>
          <UploadPanel
            project={project}
            busy={uploading}
            error={uploadError}
            onUpload={handleUpload}
          />
          <ChatPanel
            className={styles.chat}
            messages={messages}
            disabled={!project}
            busy={thinking}
            onSend={handleSend}
          />
        </div>
        <TimelineView state={timeline} duration={duration} className={styles.timeline} />
      </main>
    </div>
  )
}

export default App
