import type { Box, Effect, TimelineState, TimeRange, Track } from '../api/types'

// What the edits look and sound like at one moment, worked out in the browser so the player
// can preview them without a render. It follows the renderer's rules where they matter for
// safety (a masked target with no geometry covers the whole frame), but it is an approximation:
// the export is the exact result.

/** Keyframes further apart than this weren't seen in between: cover both ends and the gap. */
const MAX_GAP_SECONDS = 1

const inRange = (range: TimeRange, t: number) =>
  t >= range.start && (range.end === null || t < range.end)

const union = (a: Box, b: Box): Box => [
  Math.min(a[0], b[0]),
  Math.min(a[1], b[1]),
  Math.max(a[2], b[2]),
  Math.max(a[3], b[3]),
]

/** Where a track is at source time `t`. Past either end, its nearest box is held. */
export function boxAt(track: Track, t: number): Box {
  const frames = track.keyframes
  if (t <= frames[0].t) return frames[0].box
  const last = frames[frames.length - 1]
  if (t >= last.t) return last.box
  const next = frames.findIndex((k) => k.t > t)
  const a = frames[next - 1]
  const b = frames[next]
  if (b.t - a.t > MAX_GAP_SECONDS) return union(a.box, b.box)
  const f = (t - a.t) / (b.t - a.t)
  return a.box.map((v, i) => v + (b.box[i] - v) * f) as Box
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

function padded(box: Box, padding: number): Box {
  const px = (box[2] - box[0]) * padding
  const py = (box[3] - box[1]) * padding
  return [clamp01(box[0] - px), clamp01(box[1] - py), clamp01(box[2] + px), clamp01(box[3] + py)]
}

export interface PreviewMask {
  trackId: string
  /** Null: the track has no geometry, so the whole frame is covered (fail closed). */
  box: Box | null
  effect: Effect
}

/** Every mask in effect at source time `t`. */
export function masksAt(state: TimelineState, tracks: Track[], t: number): PreviewMask[] {
  const byId = new Map(tracks.map((track) => [track.track_id, track]))
  const masks: PreviewMask[] = []
  for (const [trackId, spans] of Object.entries(state.masks)) {
    const span = spans.find((s) => inRange(s, t))
    if (!span) continue
    const track = byId.get(trackId)
    masks.push({
      trackId,
      effect: span.effect,
      box: track ? padded(boxAt(track, t), span.effect.padding) : null,
    })
  }
  return masks
}

/** The cut that removes source time `t`, if any. */
export const cutAt = (state: TimelineState, t: number): TimeRange | null =>
  state.cuts.find((cut) => inRange(cut, t)) ?? null

/** True when all audio is muted at source time `t`. */
export const mutedAt = (state: TimelineState, t: number): boolean =>
  state.mutes.some((mute) => mute.speaker === null && inRange(mute, t))

/** How long the export will be: the source without its cuts. Cuts in the state never overlap. */
export function outputDuration(state: TimelineState, duration: number): number {
  const removed = state.cuts.reduce(
    (sum, cut) => sum + Math.max(0, Math.min(cut.end ?? duration, duration) - cut.start),
    0,
  )
  return Math.max(0, duration - removed)
}
