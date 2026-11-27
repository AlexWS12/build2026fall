import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api/client'
import type { ReviewReply } from '../api/types'
import {
  initialSession,
  transition,
  type ReviewData,
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
  transcribe(): Promise<void>
  /** Confirm a stretch of the review queue as it is, or cut it out. */
  review(itemId: string, action: 'confirm' | 'cut'): Promise<void>
  /** Keep only `start`..`end` of the source (the timeline's trim handles). */
  trim(start: number, end: number): Promise<void>
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

    /** The review queue; when it can't be read the reason is kept, and nothing else fails. */
    const reviewOf = (id: string): Promise<ReviewData> =>
      api.getReview(id).then(
        (review) => ({ review, reviewError: null }),
        (error: unknown) => ({ review: null, reviewError: errorText(error) }),
      )

    const sync = async (id: string): Promise<Sync> => {
      const [timeline, history, review] = await Promise.all([
        api.getState(id),
        api.getHistory(id),
        reviewOf(id),
      ])
      return { timeline, history, ...review }
    }

    const snapshot = async (id: string, videoUrl: () => string): Promise<Snapshot> => {
      const [detail, timeline, history, messages, tracks, review] = await Promise.all([
        api.getProject(id),
        api.getState(id),
        api.getHistory(id),
        api.getMessages(id),
        api.getTracks(id),
        reviewOf(id),
      ])
      return {
        detail,
        timeline,
        history,
        messages,
        tracks: tracks.tracks,
        analysed: tracks.analysed,
        ...review,
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
          const [timeline, review] = await Promise.all([api.getState(id), reviewOf(id)])
          return { type: 'history_settled', sync: { history, timeline, ...review } }
        }),
      redo: () =>
        run({ type: 'redo_started' }, async (id) => {
          const history = await api.redo(id)
          const [timeline, review] = await Promise.all([api.getState(id), reviewOf(id)])
          return { type: 'history_settled', sync: { history, timeline, ...review } }
        }),
      analyse: () =>
        run({ type: 'analysis_started' }, async (id) => {
          await api.analyse(id)
          const [{ analysed, tracks }, review] = await Promise.all([
            api.getTracks(id),
            reviewOf(id),
          ])
          return { type: 'analysis_settled', analysed, tracks, ...review }
        }),
      exportVideo: () =>
        run({ type: 'export_started' }, async (id) => ({
          type: 'export_settled',
          result: await api.exportVideo(id),
        })),
      transcribe: () =>
        run({ type: 'transcript_started' }, async (id) => ({
          type: 'transcript_settled',
          transcript: await api.transcribe(id),
        })),
      trim: (start, end) =>
        run({ type: 'trim_started' }, async (id) => {
          const history = await api.trim(id, start, end)
          const [timeline, review] = await Promise.all([api.getState(id), reviewOf(id)])
          return { type: 'trim_settled', start, end, sync: { history, timeline, ...review } }
        }),
      review: (itemId, action) =>
        run({ type: 'review_started' }, async (id) => {
          // The reply is the fresh queue: only the timeline and the history are left to read.
          let review: ReviewReply
          try {
            review = await api.reviewItem(id, itemId, action)
          } catch (error) {
            // The item is gone (the queue moved on): a full resync brings the fresh queue.
            if (!(error instanceof ApiError && error.status === 404)) throw error
            return { type: 'review_settled', sync: await sync(id), gone: true }
          }
          const [timeline, history] = await Promise.all([api.getState(id), api.getHistory(id)])
          return {
            type: 'review_settled',
            sync: { timeline, history, review, reviewError: null },
            gone: false,
          }
        }),
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
