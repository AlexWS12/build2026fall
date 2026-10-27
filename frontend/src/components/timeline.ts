import type { TimelineState, TimeRange } from '../api/types'

export interface LaneSpan extends TimeRange {
  label: string
}

export interface Lane {
  name: string
  kind: 'mask' | 'cut' | 'mute' | 'filter'
  spans: LaneSpan[]
}

/** One lane per masked track, then cuts, mutes and audio filters. */
export function lanesOf(state: TimelineState): Lane[] {
  const masks: Lane[] = Object.entries(state.masks).map(([trackId, spans]) => ({
    name: trackId,
    kind: 'mask',
    spans: spans.map((s) => ({ ...s, label: `${s.effect.type} (${s.op_id}, ${s.request_id})` })),
  }))
  return [
    ...masks,
    { name: 'Cuts', kind: 'cut', spans: state.cuts.map((c) => ({ ...c, label: 'cut' })) },
    {
      name: 'Mutes',
      kind: 'mute',
      spans: state.mutes.map((s) => ({
        ...s,
        label: `mute ${s.speaker ?? 'all audio'} (${s.op_id}, ${s.request_id})`,
      })),
    },
    {
      name: 'Audio filters',
      kind: 'filter',
      spans: state.audio_filters.map((s) => ({
        ...s,
        label: `${s.filter} (${s.op_id}, ${s.request_id})`,
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
