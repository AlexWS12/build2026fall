import type { HistoryEntry } from '../api/types'

/** The edits a request made, each kind once, in words: "add mask, cut". */
export function kindsLabel(entry: HistoryEntry): string {
  return [...new Set(entry.kinds)].map((kind) => kind.replaceAll('_', ' ')).join(', ')
}

/** What to call a request: the chat message, or the edits it made when it wasn't typed. */
export function requestLabel(entry: HistoryEntry): string {
  if (entry.text) return entry.text
  const kinds = kindsLabel(entry)
  if (!kinds) return entry.request_id
  return `${entry.source === 'user' ? 'Hand edit' : 'Edit'}: ${kinds}`
}
