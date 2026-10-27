import type { components } from './generated/schema'

// Types for the UI's side of the API. Where a route exists on the backend, its type comes from
// generated/schema.ts, which `npm run gen:api` builds from backend/src/vibe/api/openapi.json
// (D-009). Everything else is a hand-written mirror of the backend's models until its route
// lands: source of truth backend/src/vibe/contract/state.py and backend/src/vibe/storage/store.py.
// When a route lands, regenerate and replace its hand-written type with the generated one.

export type Health = components['schemas']['Health']

// Hand-written until the project routes land.

export interface VideoRef {
  sha256: string
  ext: string
  original_name: string
}

export interface ProjectInfo {
  storage_version: number
  project_id: string
  name: string
  created_at: string
  video: VideoRef
}

/** Reply to a chat request. The real shape is up to the API routes card. */
export interface ChatReply {
  request_id: string
  reply: string
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
  cuts: TimeRange[]
  mutes: MuteSpan[]
  audio_filters: AudioFilterSpan[]
  rules: Rule[]
}
