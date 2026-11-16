# home_poc — AI 주거 의사결정 서비스

대학생이 **생활 동선(통학·시간표)과 실제 주거비**를 기준으로 자취방을 고르도록 돕는 서비스입니다.
대상 대학: 이화여대 · 고려대 · 서강대 · 서울과기대

> 매물은 광고 매물이며 입주 가능·안전을 보장하지 않습니다. 실거래가는 과거 계약으로, 매물과 섞지 않고 시세 비교에만 씁니다.
> 주거 정책은 공식 데이터를 **안내만** 하고 자격을 판정하지 않습니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| 매물 추천 | 학과 건물·시간표 건물까지 도보시간, 월 고정비(월세+관리비), 면적을 점수로 계산하고 내 우선순위(1·2순위)로 순위를 매깁니다 |
| AI 상담 챗봇 | 대화로 조건을 바꾸고("더 싼 곳", "술집 많은 곳 빼줘") 결과를 설명합니다. 계산·순위는 추천 엔진이, 문장은 LLM이 맡습니다 |
| 매물 상세 | 생활 프로필 기준 도보시간, 주변 실거래 시세, 편의시설·교통, 건축물대장, 계약 전 확인, 관련 정책 |
| 시간표 사진 | 에브리타임 캡처에서 요일·시각(OpenCV 격자)과 강의실 글자(EasyOCR, 배포 서버는 Gemini)를 읽습니다. 사진은 저장하지 않습니다 |
| 의사결정 분석 | 파레토 후보, 우선순위 민감도, 조건을 풀면 늘어나는 매물 |

## 기술 스택

| 계층 | 기술 |
|---|---|
| 프론트엔드 | React 19 · TypeScript · Vite · Tailwind CSS 4 · React Router · Kakao Maps JS SDK |
| 백엔드 | Python 3.13 · FastAPI · Uvicorn · Pydantic · uv |
| AI 챗봇 | LangGraph · Gemini (기본) → EXAONE (대체) |
| 시간표 OCR | OpenCV · EasyOCR (선택 설치) |
| 데이터 | CSV·JSON 파일 · jibsa CLI · Kakao Local/도보 경로 API · 공공데이터포털(실거래가·건축물대장·상가정보) · 온통청년 API |
| 배포 | Render (BE) · Vercel (FE) |

## 폴더 구조

```
BE/          FastAPI 백엔드
  app/         API·추천 엔진(scoring)·챗봇(agent)·분석(decision)·시간표 OCR
  pipeline/    데이터 수집·가공 스크립트 (python -m pipeline.<이름>)
  tests/       pytest
FE/          React 프론트엔드
DB/          데이터 (수집 원본·가공본은 저장소에 올리지 않음)
docs/        PROJECT.md (설계·결정 이력), chatbot-rules.md (챗봇 규칙)
render.yaml  Render 배포 설정
```

## 실행 방법

### 백엔드 (`BE/`)

```bash
cd BE
uv sync                       # 시간표 OCR 까지 쓰려면: uv sync --group ocr
cp .env.example .env.local    # 필요한 키만 채운다 (아래 표)
uv run uvicorn app.main:app --port 8011
```

- API 문서: http://localhost:8011/docs
- 키가 없어도 서버는 뜹니다. 그 키를 쓰는 기능만 안내 메시지로 대신합니다.

| 환경 변수 | 쓰는 곳 |
|---|---|
| `DATA_CSV_PATH`, `WALK_TABLE_PATH` | 매물·도보시간 파일 (비우면 `DB/poc_ewha.csv` 샘플) |
| `GEMINI_API_KEY`, `EXAONE_API_KEY` | AI 상담 챗봇 |
| `KAKAO_REST_API_KEY` | 건물·자주 가는 곳 도보시간, 장소 검색 |
| `DATA_GO_KR_SERVICE_KEY` | 실거래가·건축물대장·상가정보 수집 |
| `YOUTHCENTER_API_KEY` | 청년 정책 수집 |

전체 목록과 설명은 [`BE/.env.example`](BE/.env.example)에 있습니다. 키는 `.env.local` 에만 두고 커밋하지 않습니다.

### 프론트엔드 (`FE/`)

```bash
cd FE
npm install
cp .env.example .env.local    # VITE_KAKAO_MAP_KEY(카카오 JavaScript 키), VITE_API_URL=http://localhost:8011
npm run dev                   # http://localhost:5173
```

- `VITE_API_URL` 을 비우면 서버 없이 샘플 매물만 보여 줍니다 (추천·챗봇은 동작하지 않음).
- 카카오 개발자 콘솔에서 Web 플랫폼 도메인에 `http://localhost:5173` 을 등록해야 지도가 나옵니다.

### 테스트

```bash
cd BE && uv run pytest
cd FE && npm run build && npm run lint
```

## 데이터

수집한 매물·경로·공공데이터 원본은 저장소에 올리지 않습니다.
- 로컬: `DB/` 아래 파일을 `.env.local` 경로로 지정해 씁니다. 저장소에는 샘플 `DB/poc_ewha.csv` 만 있습니다.
- 배포: 필요한 파일을 암호화한 `BE/data/deploy_bundle.bin` 을 서버가 시작할 때 `DATA_BUNDLE_KEY` 로 풉니다.
- 결측은 빈 칸(`null`)입니다. 관리비 빈 칸은 0원이 아니라 '미확인'입니다.

## 문서

- [docs/PROJECT.md](docs/PROJECT.md) — 서비스 범위, 구조, 추천 엔진, 챗봇, 데이터, 결정 이력
- [docs/chatbot-rules.md](docs/chatbot-rules.md) — 챗봇 응답 규칙
- [FE/README.md](FE/README.md) · [BE/README.md](BE/README.md) — 각 폴더 상세
