import type { components } from './generated/schema'

// Types for the UI's side of the API. Where a route exists on the backend, its type comes from
// generated/schema.ts, which `npm run gen:api` builds from backend/src/vibe/api/openapi.json
// (D-009). The generated schema marks fields that have defaults as optional even though the
// server always sends them, so the timeline state stays a hand-written mirror (source of truth:
// backend/src/vibe/contract/state.py) and the reply types below make those fields required.

type Schemas = components['schemas']

export type Health = Schemas['Health']
export type VideoRef = Schemas['VideoRef']
export type ProjectInfo = Schemas['ProjectInfo']
export type ProjectDetail = Schemas['ProjectDetail']
export type VideoSummary = Schemas['VideoSummary']
/** One tool call as it ran (F-40). */
export type StepReport = Schemas['StepReport']
/** Reply to a chat request (`POST /api/projects/{id}/requests`). */
export type ChatReply = Schemas['ChatReply']
/** One stored chat exchange (`GET /api/projects/{id}/messages`). */
export type ChatRecord = Schemas['ChatMessage']
export type HistoryEntry = Schemas['HistoryEntry']
export type HistoryReply = Schemas['HistoryReply']
export type AnalysisReply = Schemas['AnalysisReply']
/** The mask stretches to check (`GET /api/projects/{id}/review`, F-41). */
export type ReviewReply = Schemas['ReviewReply']
export type ReviewItem = Schemas['ReviewItem']
/** Body of `POST /api/projects/{id}/review/{item_id}`. */
export type ReviewAction = Schemas['ReviewAction']
export type ExportReply = Required<Schemas['ExportReply']>
/** The video's transcript with word timestamps (`POST /api/projects/{id}/transcript`, F-31). */
export type Transcript = Schemas['Transcript']

/** What a proposed edit does to the length (F-44). */
export type PreviewSummary = Schemas['PreviewSummary']

/** Normalized `[x1, y1, x2, y2]`, each 0..1. */
export type Box = [number, number, number, number]

export interface Keyframe {
  t: number
  box: Box
  conf: number
  state: Schemas['Keyframe']['state']
}

export interface Track {
  track_id: string
  label: string
  source: 'detector' | 'user'
  identity?: string | null
  keyframes: Keyframe[]
}

export interface TracksReply {
  analysed: boolean
  tracks: Track[]
}

// --- Contract: derived timeline state (schema_version 1) --------------------------------

/** Source time in seconds. `end: null` means until the end of the video. */
export interface TimeRange {
  start: number
  end: number | null
}

export interface Span extends TimeRange {
  op_id: string
  request_id: string
}

export interface Effect {
  type: 'blur' | 'pixelate' | 'box'
  padding: number
  strength: number | null
}

export interface MaskSpan extends Span {
  effect: Effect
}

export interface MuteSpan extends Span {
  /** Null = all audio. */
  speaker: string | null
}

export interface AudioFilterSpan extends Span {
  filter: 'denoise'
  strength: number | null
}

export interface Rule {
  rule_id: string
  category: string
  except_identities: string[]
  effect: Effect
  time_range: TimeRange | null
}

export interface TimelineState {
  schema_version: number
  /** Per track id: when it's masked and how. */
  masks: Record<string, MaskSpan[]>
  /** Per track id: the source-time spans a person confirmed in the review queue (F-41). */
  reviewed: Record<string, Span[]>
  cuts: TimeRange[]
  mutes: MuteSpan[]
  audio_filters: AudioFilterSpan[]
  rules: Rule[]
}

/**
 * A cut or keep waiting to be accepted or rejected (`GET /api/projects/{id}/preview`, F-44).
 * `state` is the timeline as it would be, which the player previews; `problem` is set when the
 * timeline moved on and the edit can only be discarded.
 */
export type PreviewReply = Omit<Schemas['PreviewReply'], 'state'> & { state: TimelineState }
