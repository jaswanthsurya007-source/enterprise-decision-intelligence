/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API Gateway / BFF. */
  readonly VITE_GATEWAY_URL?: string;
  /** Dev-mode static JWT attached as `Authorization: Bearer`. */
  readonly VITE_DEV_JWT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
