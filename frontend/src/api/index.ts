import { httpClient, type ApiClient } from './client'
import { createMockClient } from './mock'

export type { ApiClient } from './client'

/** `VITE_API_MODE=http` talks to the backend through the /api proxy; anything else uses the mock. */
export const defaultClient: ApiClient =
  import.meta.env.VITE_API_MODE === 'http' ? httpClient : createMockClient()
