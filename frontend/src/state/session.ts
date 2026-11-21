import type {
  ChatRecord,
  ChatReply,
  ExportReply,
  HistoryEntry,
  HistoryReply,
  ProjectDetail,
  ReviewReply,
  StepReport,
  TimelineState,
  Track,
  Transcript,
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
 *                         (requesting | undoing | redoing | analysing | transcribing | exporting | reviewing)
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
  | { kind: 'transcribing' }
  | { kind: 'exporting' }
  | { kind: 'reviewing' }
  /** A hand edit from the player: removing the masks a person clicked (F-42). */
  | { kind: 'removing'; label: string }

export type ExportState =
  | { kind: 'none' }
  /** `stale`: the timeline changed after this file was rendered. */
  | { kind: 'ready'; result: ExportReply; stale: boolean }
  | { kind: 'failed'; message: string }

/**
 * The review queue (F-41) is extra reading: when it can't be read, `review` is null and
 * `reviewError` says why, and the rest of the project still loads.
 */
export interface ReviewData {
  review: ReviewReply | null
  reviewError: string | null
}

/** What the server knows about a project, fetched together when it opens. */
export interface Snapshot extends ReviewData {
  detail: ProjectDetail
  videoUrl: string
  timeline: TimelineState
  history: HistoryReply
  tracks: Track[]
  analysed: boolean
  messages: ChatRecord[]
}

export interface OpenProject extends ReviewData {
  detail: ProjectDetail
  videoUrl: string
  timeline: TimelineState
  history: HistoryReply
  tracks: Track[]
  analysed: boolean
  /** The video's transcript (F-31), once it has been asked for in this session. */
  transcript: Transcript | null
  chat: ChatEntry[]
  activity: Activity
  export: ExportState
}

export type Session =
  | { status: 'empty'; error: string | null }
  /** Uploading or reopening. `fallback` is the project that stays on screen meanwhile. */
  | { status: 'opening'; label: string; fallback: OpenProject | null }
  | { status: 'open'; project: OpenProject; error: string | null }

/** The timeline, its history and the review queue travel together, so they can't drift apart. */
export interface Sync extends ReviewData {
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
  | { type: 'remove_started'; label: string }
  | { type: 'remove_settled'; sync: Sync }
  | { type: 'analysis_started' }
  | ({ type: 'analysis_settled'; analysed: boolean; tracks: Track[] } & ReviewData)
  | { type: 'transcript_started' }
  | { type: 'transcript_settled'; transcript: Transcript }
  | { type: 'export_started' }
  | { type: 'export_settled'; result: ExportReply }
  | { type: 'review_started' }
  | { type: 'review_settled'; sync: Sync; gone: boolean }
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
    case 'remove_started':
      if (!idle) return project
      return { ...project, activity: { kind: 'removing', label: event.label } }
    case 'remove_settled': {
      if (activity.kind !== 'removing') return project
      const text = `Removed the blur on ${activity.label}. Undo brings it back.`
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
        review: event.review,
        reviewError: event.reviewError,
        chat: [...project.chat, { kind: 'note', id: nextId(project), text }],
      }
    }
    case 'transcript_started':
      if (!idle) return project
      return { ...project, activity: { kind: 'transcribing' } }
    case 'transcript_settled':
      if (activity.kind !== 'transcribing') return project
      return { ...project, activity: IDLE, transcript: event.transcript }
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
    case 'review_started':
      if (!idle) return project
      return { ...project, activity: { kind: 'reviewing' } }
    case 'review_settled':
      if (activity.kind !== 'reviewing') return project
      return synced(
        {
          ...project,
          activity: IDLE,
          chat: event.gone
            ? [
                ...project.chat,
                {
                  kind: 'note',
                  id: nextId(project),
                  text: 'That stretch was already handled. The review queue was refreshed.',
                },
              ]
            : project.chat,
        },
        event.sync,
      )
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
    review: snapshot.review,
    reviewError: snapshot.reviewError,
    transcript: null,
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
  return {
    ...project,
    timeline: sync.timeline,
    history: sync.history,
    review: sync.review,
    reviewError: sync.reviewError,
    export: exported,
  }
}

const nextId = (project: OpenProject) => `e${project.chat.length}`

const KIND_LABEL: Record<string, string> = { confirm_mask: 'a review confirmation', cut: 'a cut' }
const named = (kinds: string[]) => [...new Set(kinds.map((k) => KIND_LABEL[k] ?? k))].join(', ')

/** What the request whose status flipped between two histories was, in the user's words. */
function changedRequest(before: HistoryReply, after: HistoryReply): string | null {
  const was = new Map(before.requests.map((r) => [r.request_id, r.status]))
  const changed = after.requests.find((r) => was.get(r.request_id) !== r.status)
  if (!changed) return null
  return changed.text ? `"${changed.text}"` : (changed.reply ?? named(changed.kinds))
}

function failureText(activity: Activity, message: string): string {
  const what: Record<Exclude<Activity['kind'], 'idle' | 'exporting'>, string> = {
    requesting: 'Request failed',
    undoing: 'Undo failed',
    redoing: 'Redo failed',
    analysing: 'Analysis failed',
    transcribing: 'Transcription failed',
    reviewing: 'Review failed',
    removing: 'Removing the blur failed',
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
export const canEditByHand = (session: Session): boolean => idleProject(session) !== null
export const canOpen = (session: Session): boolean => !isBusy(session)

const ACTIVITY_LABEL: Record<Activity['kind'], string | null> = {
  idle: null,
  requesting: 'Working on it…',
  undoing: 'Undoing…',
  redoing: 'Redoing…',
  analysing: 'Analysing the video…',
  transcribing: 'Waiting for the transcript…',
  exporting: 'Rendering the export…',
  reviewing: 'Applying review…',
  removing: 'Removing the blur…',
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

/** The request Undo would take back: undo is linear, so it is the latest one still applied. */
export function undoTarget(history: HistoryReply): string | null {
  if (!history.can_undo) return null
  return history.requests.findLast((r) => r.status === 'applied')?.request_id ?? null
}

/*
 * What Redo would bring back, worked out from the statuses alone. The backend keeps redo as a
 * stack (vibe/contract/fold.py `history`): Redo brings back the most recently undone request,
 * and a new request clears the stack. `HistoryReply` doesn't say which request that is, and
 * statuses in log order can't always tell: "A, undo A, B, undo B" and "A, B, undo B, undo A"
 * both read `[A undone, B undone]`, but the first redoes B (A is gone for good) and the second
 * redoes A. What can be told for certain:
 * - an undone request logged before the latest applied one is gone for good: undo is linear,
 *   so it was undone before that applied request was made, and making it cleared redo;
 * - when nothing can be redone, every undone request is gone for good;
 * - when exactly one undone request comes after the latest applied one, Redo brings back that one.
 * Anything else is left unmarked. If the reply ever names the redo target, use it instead.
 */

/** The undone requests logged after the latest one still applied. */
function trailingUndone(history: HistoryReply): HistoryEntry[] {
  const lastApplied = history.requests.findLastIndex((r) => r.status === 'applied')
  return history.requests.slice(lastApplied + 1)
}

/** The request Redo would bring back, or null when there is none or it can't be told for certain. */
export function redoTarget(history: HistoryReply): string | null {
  if (!history.can_redo) return null
  const trailing = trailingUndone(history)
  return trailing.length === 1 ? trailing[0].request_id : null
}

/** Undone requests that Redo can no longer bring back, because a newer request came after them. */
export function undoneForGood(history: HistoryReply): Set<string> {
  const mayRedo = new Set(history.can_redo ? trailingUndone(history).map((r) => r.request_id) : [])
  return new Set(
    history.requests
      .filter((r) => r.status === 'undone' && !mayRedo.has(r.request_id))
      .map((r) => r.request_id),
  )
}
