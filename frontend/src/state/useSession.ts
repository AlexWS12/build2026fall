import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../api'
import {
  initialSession,
  transition,
  type Session,
  type SessionEvent,
  type Snapshot,
  type Sync,
} from './session'

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error))

const HASH_PREFIX = '#/p/'

export interface SessionCommands {
  upload(file: File): Promise<void>
  open(projectId: string): Promise<void>
  send(text: string): Promise<void>
  undo(): Promise<void>
  redo(): Promise<void>
  analyse(): Promise<void>
  exportVideo(): Promise<void>
  /** Leave the open project and go back to the start screen. */
  close(): void
}

/**
 * Runs the session machine (session.ts) against an ApiClient. Each command sends a `*_started`
 * event first and stops if the machine refuses it, so a double click or a request made while
 * the project is busy never reaches the server.
 */
export function useSession(api: ApiClient): { session: Session; commands: SessionCommands } {
  const [session, setSession] = useState<Session>(initialSession)
  const current = useRef(session)

  const dispatch = useCallback((event: SessionEvent): boolean => {
    const next = transition(current.current, event)
    if (next === current.current) return false
    current.current = next
    setSession(next)
    return true
  }, [])

  const commands = useMemo<SessionCommands>(() => {
    const projectId = () =>
      current.current.status === 'open' ? current.current.project.detail.info.project_id : null

    const sync = async (id: string): Promise<Sync> => {
      const [timeline, history] = await Promise.all([api.getState(id), api.getHistory(id)])
      return { timeline, history }
    }

    const snapshot = async (id: string, videoUrl: () => string): Promise<Snapshot> => {
      const [detail, timeline, history, messages, tracks] = await Promise.all([
        api.getProject(id),
        api.getState(id),
        api.getHistory(id),
        api.getMessages(id),
        api.getTracks(id),
      ])
      return {
        detail,
        timeline,
        history,
        messages,
        tracks: tracks.tracks,
        analysed: tracks.analysed,
        videoUrl: videoUrl(),
      }
    }

    /** One activity on the open project: started, then settled or failed (with a resync). */
    const run = async (started: SessionEvent, work: (id: string) => Promise<SessionEvent>) => {
      const id = projectId()
      if (id === null || !dispatch(started)) return
      try {
        dispatch(await work(id))
      } catch (error) {
        const resynced = await sync(id).catch(() => undefined)
        dispatch({ type: 'activity_failed', message: errorText(error), sync: resynced })
      }
    }

    return {
      async upload(file) {
        if (!dispatch({ type: 'open_started', label: `Uploading ${file.name}…` })) return
        try {
          const created = await api.createProject(file)
          // The player plays the local file: no need to download what was just uploaded.
          const loaded = await snapshot(created.project_id, () => URL.createObjectURL(file))
          dispatch({ type: 'open_succeeded', snapshot: loaded })
        } catch (error) {
          dispatch({ type: 'open_failed', message: `Upload failed: ${errorText(error)}` })
        }
      },
      async open(id) {
        if (!dispatch({ type: 'open_started', label: 'Opening the project…' })) return
        try {
          const loaded = await snapshot(id, () => api.videoUrl(id))
          dispatch({ type: 'open_succeeded', snapshot: loaded })
        } catch (error) {
          dispatch({ type: 'open_failed', message: `Couldn't open the project: ${errorText(error)}` })
        }
      },
      send: (text) =>
        run({ type: 'request_started', text }, async (id) => {
          const reply = await api.sendRequest(id, text)
          return { type: 'request_settled', reply, sync: await sync(id) }
        }),
      undo: () =>
        run({ type: 'undo_started' }, async (id) => {
          const history = await api.undo(id)
          return { type: 'history_settled', sync: { history, timeline: await api.getState(id) } }
        }),
      redo: () =>
        run({ type: 'redo_started' }, async (id) => {
          const history = await api.redo(id)
          return { type: 'history_settled', sync: { history, timeline: await api.getState(id) } }
        }),
      analyse: () =>
        run({ type: 'analysis_started' }, async (id) => {
          await api.analyse(id)
          const { analysed, tracks } = await api.getTracks(id)
          return { type: 'analysis_settled', analysed, tracks }
        }),
      exportVideo: () =>
        run({ type: 'export_started' }, async (id) => ({
          type: 'export_settled',
          result: await api.exportVideo(id),
        })),
      close() {
        if (dispatch({ type: 'closed' })) window.history.replaceState(null, '', ' ')
      },
    }
  }, [api, dispatch])

  const project = session.status === 'open' ? session.project : null
  const videoUrl = project?.videoUrl ?? null
  const openId = project?.detail.info.project_id ?? null

  // An uploaded file plays from an object URL; free it when the project is replaced.
  useEffect(() => {
    if (!videoUrl?.startsWith('blob:')) return
    return () => URL.revokeObjectURL(videoUrl)
  }, [videoUrl])

  // A project the server can serve again is kept in the address, so a reload reopens it.
  useEffect(() => {
    if (openId === null || !api.videoUrl(openId)) return
    window.history.replaceState(null, '', `${HASH_PREFIX}${encodeURIComponent(openId)}`)
  }, [api, openId])

  useEffect(() => {
    const { hash } = window.location
    if (!hash.startsWith(HASH_PREFIX)) return
    const id = decodeURIComponent(hash.slice(HASH_PREFIX.length))
    if (!id) return
    void commands.open(id).then(() => {
      // A project that's gone shouldn't be retried on every reload.
      if (current.current.status !== 'open') window.history.replaceState(null, '', ' ')
    })
  }, [commands])

  return { session, commands }
}
