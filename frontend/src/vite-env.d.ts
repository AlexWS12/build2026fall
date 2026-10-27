/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** `http` = real backend through the /api proxy; unset or anything else = mock API. */
  readonly VITE_API_MODE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
