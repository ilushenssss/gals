/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Подложка карты. Пусто — работаем без тайлов (закрытый контур). */
  readonly VITE_TILE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
