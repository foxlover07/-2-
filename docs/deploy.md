# 배포 (Render BE + Vercel FE)

> 2026-11-08 기준. 이 저장소의 `feat/data-pipeline` 을 새로 받아 BE 설치·실행, FE 빌드까지 로컬에서 확인했다.
> 실제 배포는 Render·Vercel 대시보드에서 한다 (CLI 없음).

## 순서

BE 를 먼저 띄워 주소를 받고 → FE 에 그 주소를 넣고 → FE 주소를 다시 BE CORS 와 카카오 도메인에 넣는다.

### 1. BE — Render

1. Render 대시보드 → **New → Blueprint** → 이 GitHub 저장소 선택. 루트의 `render.yaml` 을 읽는다
   (서비스 `home-poc-be`, Python, `rootDir: BE`, 무료 플랜, 헬스 체크 `/health`).
2. **브랜치**: Blueprint 는 저장소 기본 브랜치(`main`)를 쓴다. 지금 작업 브랜치로 배포하려면
   서비스 **Settings → Build & Deploy → Branch** 를 `feat/data-pipeline` 으로 바꾼다.
3. 환경 변수 (대시보드에서 입력, 저장소에 넣지 않는다)

   | 키 | 값 |
   |---|---|
   | `CORS_ORIGINS` | FE 주소. 처음엔 비워 두고 4단계 후 `https://<vercel 주소>` 입력 (여러 개면 쉼표) |
   | `KAKAO_REST_API_KEY` | 비워 둬도 된다. 배포 서버는 Kakao 를 호출하지 않는다 (파이프라인 전용) |

4. 배포가 끝나면 `https://<render 주소>/health` 가 `{"status":"ok"}` 인지 확인.

### 2. FE — Vercel

1. Vercel → **Add New → Project** → 이 저장소 → **Root Directory: `FE`** (Framework: Vite 자동 인식).
2. 환경 변수

   | 키 | 값 |
   |---|---|
   | `VITE_API_URL` | `https://<render 주소>` (끝에 `/` 없이) |
   | `VITE_KAKAO_MAP_KEY` | 카카오 **JavaScript** 키 |

   `VITE_*` 는 빌드할 때 들어가므로 값을 바꾸면 **다시 배포(Redeploy)** 해야 한다.
3. **브랜치**: Settings → Git → Production Branch 를 `feat/data-pipeline` 으로 (또는 그 브랜치의 Preview 배포 주소 사용).
4. `vercel.json` 이 모든 경로를 `index.html` 로 돌려서 `/profile`, `/compare` 새로고침도 된다.

### 3. 서로 연결

- Render `CORS_ORIGINS` 에 Vercel 주소 입력 → BE 재시작.
  Preview 배포 주소는 배포마다 바뀌므로, 고정 주소(Production 도메인)를 쓴다.
- 카카오 개발자 콘솔 → 앱 → 플랫폼 → **Web 사이트 도메인**에 Vercel 주소 추가. 안 하면 지도가
  "카카오맵 SDK 스크립트를 불러오지 못했습니다" 로 나온다.

## 확인

- FE `/` : 지도와 매물 목록 (PoC 10건)
- FE `/profile` 저장 → `/compare` 에 추천 결과
- 브라우저 콘솔에 CORS 에러가 없으면 연결 완료

## 알아둘 점

- **배포 데이터는 PoC 10건(`DB/poc_ewha.csv`)** 이다. 직방 수집 매물(282건)과 Kakao 도보 경로 표는 `.gitignore`
  대상이라 배포본에 없다. 공개 배포에 넣으려면 jibsa(비공식 수집) 이용 범위와 Kakao 응답 저장·재사용 정책을 먼저 확인한다.
  넣게 되면 `DATA_CSV_PATH`, `WALK_TABLE_PATH` 환경 변수로 바꾼다 (BE/.env.example).
- Render 무료 플랜은 15분쯤 요청이 없으면 잠든다. 첫 요청이 30초~1분 걸리며, FE 는 5초가 지나면 그 안내를 보여 준다.
- 배포 BE 는 LLM·Kakao 를 호출하지 않는다 (추천 계산은 전부 코드).
