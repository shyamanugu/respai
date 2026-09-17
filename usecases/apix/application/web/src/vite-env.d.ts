/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DASHBOARD_API_URL?: string;
  readonly VITE_CHAT_API_URL?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
