import type { Box, Effect, Track } from '../api/types'
import { boxAt } from './preview'

// Click or draw a box to target (F-14): the helpers TargetLayer uses, apart so they can be tested
// and so the component file only exports components.

/** A drag shorter than this (a fraction of the picture) is a click, not a box. */
export const CLICK_SLOP = 0.01

/** How long past its first or last keyframe a target still counts as on screen, like the
 * renderer's hold before a box is marked lost (render/geometry.py, HOLD_SECONDS). */
const SEEN_SLACK_SECONDS = 0.5

export type EffectType = Effect['type']

/** The targets the server follows that are on screen at `t`, with their box then. */
export function targetsAt(tracks: Track[], t: number): { track: Track; box: Box }[] {
  return tracks
    .filter((track) => {
      const first = track.keyframes[0].t
      const last = track.keyframes[track.keyframes.length - 1].t
      return t >= first - SEEN_SLACK_SECONDS && t <= last + SEEN_SLACK_SECONDS
    })
    .map((track) => ({ track, box: boxAt(track, t) }))
}
