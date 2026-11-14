import type {
  ChatRecord,
  ChatReply,
  ExportReply,
  HistoryReply,
  ProjectDetail,
  StepReport,
  TimelineState,
  Track,
} from '../api/types'

/**
 * The editing session as one explicit state machine.
 *
 * Before this, the UI kept a handful of independent flags (`project`, `uploading`, `thinking`,
 * `uploadError`...) and every new slow action added another flag plus rules about which
 * combinations were allowed. Here a session is in exactly one state, a project does exactly one
 * thing at a time (the server enforces the same with 409), and `transition` is the only place
 * that says which event is legal when. An illegal event returns the same object, so callers can
 * tell it was refused.
 *
 *   empty ──open_started──► opening ──open_succeeded──► open(idle)
 *     ▲                        │                          │  ▲
 *     └──────open_failed───────┘        *_started ────────┘  └──── *_settled / activity_failed
 *                                       (requesting | undoing | redoing | analysing | exporting)
 */

export type ChatEntry =
  | { kind: 'user'; id: string; text: string }
  | {
      kind: 'agent'
      id: string
      requestId: string
      status: ChatReply['status']
      text: string
      steps: StepReport[]
    }
  /** The request itself failed (network, server error). Nothing was changed. */
  | { kind: 'error'; id: string; text: string }
  /** Something the UI did, not the agent: an undo, a redo, an analysis. */
  | { kind: 'note'; id: string; text: string }

/** What an open project is doing right now. One thing at a time, like the server. */
export type Activity =
  | { kind: 'idle' }
  | { kind: 'requesting'; text: string }
  | { kind: 'undoing' }
  | { kind: 'redoing' }
  | { kind: 'analysing' }
  | { kind: 'exporting' }

export type ExportState =
  | { kind: 'none' }
  /** `stale`: the timeline changed after this file was rendered. */
  | { kind: 'ready'; result: ExportReply; stale: boolean }
  | { kind: 'failed'; message: string }

/** What the server knows about a project, fetched together when it opens. */
export interface Snapshot {
  detail: ProjectDetail
  videoUrl: string
  timeline: TimelineState
  history: HistoryReply
  tracks: Track[]
  analysed: boolean
  messages: ChatRecord[]
}

export interface OpenProject {
  detail: ProjectDetail
  videoUrl: string
  timeline: TimelineState
  history: HistoryReply
  tracks: Track[]
  analysed: boolean
  chat: ChatEntry[]
  activity: Activity
  export: ExportState
}

export type Session =
  | { status: 'empty'; error: string | null }
  /** Uploading or reopening. `fallback` is the project that stays on screen meanwhile. */
  | { status: 'opening'; label: string; fallback: OpenProject | null }
  | { status: 'open'; project: OpenProject; error: string | null }

/** The timeline and its history always travel together, so they can't drift apart. */
export interface Sync {
  timeline: TimelineState
  history: HistoryReply
}

export type SessionEvent =
  | { type: 'open_started'; label: string }
  | { type: 'open_succeeded'; snapshot: Snapshot }
  | { type: 'open_failed'; message: string }
  | { type: 'closed' }
  | { type: 'request_started'; text: string }
  | { type: 'request_settled'; reply: ChatReply; sync: Sync }
  | { type: 'undo_started' }
  | { type: 'redo_started' }
  | { type: 'history_settled'; sync: Sync }
  | { type: 'analysis_started' }
  | { type: 'analysis_settled'; analysed: boolean; tracks: Track[] }
  | { type: 'export_started' }
  | { type: 'export_settled'; result: ExportReply }
  /** The activity in flight failed. `sync`, when the server could still be read, is the truth. */
  | { type: 'activity_failed'; message: string; sync?: Sync }

export const initialSession: Session = { status: 'empty', error: null }

const IDLE: Activity = { kind: 'idle' }

export function transition(session: Session, event: SessionEvent): Session {
  switch (event.type) {
    case 'open_started':
      if (session.status === 'opening') return session
      if (session.status === 'open' && session.project.activity.kind !== 'idle') return session
      return {
        status: 'opening',
        label: event.label,
        fallback: session.status === 'open' ? session.project : null,
      }
    case 'open_succeeded':
      if (session.status !== 'opening') return session
      return { status: 'open', project: fromSnapshot(event.snapshot), error: null }
    case 'open_failed':
      if (session.status !== 'opening') return session
      return session.fallback
        ? { status: 'open', project: session.fallback, error: event.message }
        : { status: 'empty', error: event.message }
    case 'closed':
      if (session.status !== 'open' || session.project.activity.kind !== 'idle') return session
      return initialSession
    default:
      if (session.status !== 'open') return session
      return withProject(session, projectTransition(session.project, event))
  }
}

type ProjectEvent = Exclude<
  SessionEvent,
  { type: 'open_started' | 'open_succeeded' | 'open_failed' | 'closed' }
>

function projectTransition(project: OpenProject, event: ProjectEvent): OpenProject {
  const { activity } = project
  const idle = activity.kind === 'idle'
  switch (event.type) {
    case 'request_started':
      if (!idle) return project
      return {
        ...project,
        activity: { kind: 'requesting', text: event.text },
        chat: [...project.chat, { kind: 'user', id: nextId(project), text: event.text }],
      }
    case 'request_settled': {
      if (activity.kind !== 'requesting') return project
      const { reply } = event
      return synced(
        {
          ...project,
          activity: IDLE,
          chat: [
            ...project.chat,
            {
              kind: 'agent',
              id: nextId(project),
              requestId: reply.request_id,
              status: reply.status,
              text: reply.reply,
              steps: reply.steps ?? [],
            },
          ],
        },
        event.sync,
      )
    }
    case 'undo_started':
      if (!idle || !project.history.can_undo) return project
      return { ...project, activity: { kind: 'undoing' } }
    case 'redo_started':
      if (!idle || !project.history.can_redo) return project
      return { ...project, activity: { kind: 'redoing' } }
    case 'history_settled': {
      if (activity.kind !== 'undoing' && activity.kind !== 'redoing') return project
      const verb = activity.kind === 'undoing' ? 'Undid' : 'Redid'
      const what = changedRequest(project.history, event.sync.history)
      const text = what ? `${verb}: ${what}` : `${verb} the last request.`
      return synced(
        {
          ...project,
          activity: IDLE,
          chat: [...project.chat, { kind: 'note', id: nextId(project), text }],
        },
        event.sync,
      )
    }
    case 'analysis_started':
      if (!idle) return project
      return { ...project, activity: { kind: 'analysing' } }
    case 'analysis_settled': {
      if (activity.kind !== 'analysing') return project
      const count = event.tracks.length
      const text = event.analysed
        ? `Analysed the video: ${count} ${count === 1 ? 'target' : 'targets'} found.`
        : 'The video could not be analysed.'
      return {
        ...project,
        activity: IDLE,
        analysed: event.analysed,
        tracks: event.tracks,
        chat: [...project.chat, { kind: 'note', id: nextId(project), text }],
      }
    }
    case 'export_started':
      if (!idle) return project
      return { ...project, activity: { kind: 'exporting' } }
    case 'export_settled':
      if (activity.kind !== 'exporting') return project
      return {
        ...project,
        activity: IDLE,
        export: { kind: 'ready', result: event.result, stale: false },
      }
    case 'activity_failed': {
      if (idle) return project
      const failed: OpenProject =
        activity.kind === 'exporting'
          ? { ...project, activity: IDLE, export: { kind: 'failed', message: event.message } }
          : {
              ...project,
              activity: IDLE,
              chat: [
                ...project.chat,
                { kind: 'error', id: nextId(project), text: failureText(activity, event.message) },
              ],
            }
      return event.sync ? synced(failed, event.sync) : failed
    }
  }
}

function fromSnapshot(snapshot: Snapshot): OpenProject {
  const chat: ChatEntry[] = snapshot.messages.flatMap((message, index): ChatEntry[] => [
    { kind: 'user', id: `m${index}u`, text: message.text },
    {
      kind: 'agent',
      id: `m${index}a`,
      requestId: message.request_id,
      status: message.status,
      text: message.reply,
      steps: message.steps ?? [],
    },
  ])
  return {
    detail: snapshot.detail,
    videoUrl: snapshot.videoUrl,
    timeline: snapshot.timeline,
    history: snapshot.history,
    tracks: snapshot.tracks,
    analysed: snapshot.analysed,
    chat,
    activity: IDLE,
    export: { kind: 'none' },
  }
}

const withProject = (
  session: Extract<Session, { status: 'open' }>,
  project: OpenProject,
): Session => (project === session.project ? session : { ...session, project, error: null })

/** Takes the server's timeline and history, and marks a finished export stale if they moved. */
function synced(project: OpenProject, sync: Sync): OpenProject {
  const moved = JSON.stringify(sync.timeline) !== JSON.stringify(project.timeline)
  const exported =
    moved && project.export.kind === 'ready' ? { ...project.export, stale: true } : project.export
  return { ...project, timeline: sync.timeline, history: sync.history, export: exported }
}

const nextId = (project: OpenProject) => `e${project.chat.length}`

/** What the request whose status flipped between two histories was, in the user's words. */
function changedRequest(before: HistoryReply, after: HistoryReply): string | null {
  const was = new Map(before.requests.map((r) => [r.request_id, r.status]))
  const changed = after.requests.find((r) => was.get(r.request_id) !== r.status)
  if (!changed) return null
  return changed.text ? `"${changed.text}"` : (changed.reply ?? changed.kinds.join(', '))
}

function failureText(activity: Activity, message: string): string {
  const what: Record<Exclude<Activity['kind'], 'idle' | 'exporting'>, string> = {
    requesting: 'Request failed',
    undoing: 'Undo failed',
    redoing: 'Redo failed',
    analysing: 'Analysis failed',
  }
  const label = activity.kind === 'idle' || activity.kind === 'exporting' ? '' : what[activity.kind]
  return `${label}: ${message}`
}

// --- What the UI may do, derived from the one state ------------------------------------------

export const openProject = (session: Session): OpenProject | null =>
  session.status === 'open' ? session.project : session.status === 'opening' ? session.fallback : null

/** True while nothing else can start: opening a project, or the open one is busy. */
export const isBusy = (session: Session): boolean =>
  session.status === 'opening' ||
  (session.status === 'open' && session.project.activity.kind !== 'idle')

const idleProject = (session: Session): OpenProject | null =>
  session.status === 'open' && session.project.activity.kind === 'idle' ? session.project : null

export const canSend = (session: Session): boolean => idleProject(session) !== null
export const canUndo = (session: Session): boolean => !!idleProject(session)?.history.can_undo
export const canRedo = (session: Session): boolean => !!idleProject(session)?.history.can_redo
export const canExport = (session: Session): boolean => idleProject(session) !== null
export const canOpen = (session: Session): boolean => !isBusy(session)

const ACTIVITY_LABEL: Record<Activity['kind'], string | null> = {
  idle: null,
  requesting: 'Working on it…',
  undoing: 'Undoing…',
  redoing: 'Redoing…',
  analysing: 'Analysing the video…',
  exporting: 'Rendering the export…',
}

/** One line saying what's in flight, or null when idle. */
export function busyLabel(session: Session): string | null {
  if (session.status === 'opening') return session.label
  if (session.status === 'open') return ACTIVITY_LABEL[session.project.activity.kind]
  return null
}

/** Status of each request by id, for marking undone replies in the chat. */
export function requestStatuses(history: HistoryReply): Map<string, 'applied' | 'undone'> {
  return new Map(history.requests.map((r) => [r.request_id, r.status]))
}
