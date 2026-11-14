import type { TimelineState, TimeRange, Track } from '../api/types'

export interface LaneSpan extends TimeRange {
  label: string
  /** The request that made it, when the state says (cuts don't). */
  requestId: string | null
}

export interface Lane {
  /** Stable id: the track id for a mask lane, the lane name otherwise. */
  id: string
  name: string
  kind: 'mask' | 'cut' | 'mute' | 'filter'
  spans: LaneSpan[]
}

/** "face 1", "face 2", "person 1"... for tracks the analysis knows; the id for the rest. */
function trackNames(tracks: Track[]): Map<string, string> {
  const counts = new Map<string, number>()
  const names = new Map<string, string>()
  for (const track of tracks) {
    const n = (counts.get(track.label) ?? 0) + 1
    counts.set(track.label, n)
    names.set(track.track_id, `${track.label.replaceAll('_', ' ')} ${n}`)
  }
  return names
}

/** One lane per masked track, then cuts, mutes and audio filters. */
export function lanesOf(state: TimelineState, tracks: Track[] = []): Lane[] {
  const names = trackNames(tracks)
  const masks: Lane[] = Object.entries(state.masks).map(([trackId, spans]) => ({
    id: trackId,
    name: names.get(trackId) ?? trackId,
    kind: 'mask',
    spans: spans.map((s) => ({
      ...s,
      label: `${s.effect.type} (${s.op_id}, ${s.request_id})`,
      requestId: s.request_id,
    })),
  }))
  return [
    ...masks,
    {
      id: 'Cuts',
      name: 'Cuts',
      kind: 'cut',
      spans: state.cuts.map((c) => ({ ...c, label: 'cut', requestId: null })),
    },
    {
      id: 'Mutes',
      name: 'Mutes',
      kind: 'mute',
      spans: state.mutes.map((s) => ({
        ...s,
        label: `mute ${s.speaker ?? 'all audio'} (${s.op_id}, ${s.request_id})`,
        requestId: s.request_id,
      })),
    },
    {
      id: 'Audio filters',
      name: 'Audio filters',
      kind: 'filter',
      spans: state.audio_filters.map((s) => ({
        ...s,
        label: `${s.filter} (${s.op_id}, ${s.request_id})`,
        requestId: s.request_id,
      })),
    },
  ]
}

/** Without the video's duration, fit the latest time the state mentions, plus some room. */
export function fallbackDuration(state: TimelineState): number {
  const ranges = [
    ...Object.values(state.masks).flat(),
    ...state.cuts,
    ...state.mutes,
    ...state.audio_filters,
  ]
  const latest = Math.max(0, ...ranges.map((r) => r.end ?? r.start))
  return Math.max(10, Math.ceil(latest * 1.1))
}

export const formatTime = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
