/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_KAKAO_MAP_KEY?: string
  // FastAPI(BE) 주소. 비우면 로컬 데이터(src/data/properties.ts)를 쓴다.
  readonly VITE_API_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
