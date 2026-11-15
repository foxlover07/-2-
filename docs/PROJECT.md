# home_poc — AI 주거 의사결정 서비스 (통합 문서)

> 작성 2026-11-16. 아래 문서들을 하나로 합친 **현재 기준 문서**다.
> 대상 코드: worktree `home_poc-integrated`, 브랜치 `feat/decision-support` (마지막 커밋 `ac762c7`, 그 뒤 **미커밋 변경 다수**).
> 상태 표기: ✅ 구현·검증 · 🟡 부분 · ⬜ 미구현 · ⛔ 외부 권한·키·결정 대기 · (확인 필요) = 문서끼리 다르거나 코드로 확정 못 함

---

## 0. 문서 안내

### 0.1 이 문서가 대신하는 문서

| 원래 문서 | 위치 | 처리 |
|---|---|---|
| `DEVELOPMENT_PROGRESS.md` (11-15~16 진행·결정) | `home_poc/` | 8장(결정)·9장(진행)과 각 장 본문으로 옮김. 가장 최신 기록이라 다른 문서와 다르면 이것을 따름 |
| `docs/recommend-integrated-design.md` (11-13 통합안) | `home_poc-integrated/` | 3장. '챗봇 미완성' 표시는 낡아서 4장으로 대체 |
| `docs/agent-design.md` (11-10) | `home_poc/` | 4장. v3 호출·Gemini 단독·시간표 Gemini 읽기 등은 삭제 |
| `docs/design-overview.md` (11-10) | `home_poc/` | 2·5·7장. v3 설계·데이터 현황(11-10)은 삭제 |
| `FE/FRONTEND_SPEC.md` (11-14) | `home_poc/` | 6장으로 요약. 11-15~16 변경 반영 |
| `docs/recommend-score-design.md` (v2, 11-07) | `home_poc-integrated/` | 3.9 '이전 설계' 메모로 요약 |
| `docs/student-criteria.md` (초기 근거) | `home_poc-integrated/` | 1.4 표로 옮김 |
| `docs/deploy.md` (11-08) | `home_poc-integrated/` | 7장 |
| `CLAUDE_START_PROMPT.md` (11-06 Codex → Claude 인수인계) | `home_poc/` | 옮길 내용 없음 (PBO 중심 계획·정책 매칭 제외 등은 이후 결정으로 바뀜. 지속 원칙은 `CLAUDE.md` 에 있음) |

### 0.2 기준 순서

**코드 > 이 문서 > `CLAUDE.md` 7장의 '목표 설계'**. 운영 원칙(승인 기반 진행, 데이터 보호, 키 관리)은 `CLAUDE.md` 를 따른다.
숫자는 모두 코드 설정값(`BE/app/scoring_config.py` 등)이 정답이다. 이 문서의 숫자가 코드와 다르면 코드를 따르고 이 문서를 고친다.

### 0.3 작업 위치

| 트리 | 브랜치 | 용도 |
|---|---|---|
| `c:\Users\user\Desktop\home_poc-integrated` | `feat/decision-support` (origin 에 푸시됨, PR 없음) | **새 개발은 여기서** |
| `c:\Users\user\Desktop\home_poc` | `feat/data-pipeline` (38a2a62 + 미커밋) | 원래 트리. 미커밋 챗봇은 v3 기반 옛 상태. Python venv `home/` 은 여기 있음 |

- Python: `home_poc/home/` venv (Python 3.13.5). worktree 에서 `cd BE && ../../home_poc/home/Scripts/python.exe -X utf8 -m pytest -q`.
- 팀 공유 저장소의 `main` 으로 PR 하지 않는다. 남의 브랜치·main 에 푸시하지 않는다.
- 미커밋 작업은 오류·정리를 마친 뒤 커밋한다 (11-15 사용자 결정). 커밋 메시지에 AI 표기를 넣지 않는다.

---

## 1. 서비스 개요 · 범위

### 1.1 한 줄 정의

대학생·사회초년생이 학교 주변 월세 매물을 **지도에서 탐색**하고, 자기 **조건(유지비·통학 시간·면적)과 우선순위**로 추천을 받아 **비교·분석**하는 서비스. 챗봇으로 조건을 말로 바꾸고 결과 설명·분석·정책 안내를 받는다.

### 1.2 대상

| 항목 | 값 |
|---|---|
| 대학 | 이화여자대학교 · 고려대학교 · 서강대학교 · 서울과학기술대학교 (4곳) |
| 매물 | 직방(jibsa CLI) 광고 매물, 월세 **원룸·오피스텔**, 보증금 3,000만 원 이하, 지번 주소, 캠퍼스 직선 2km 안 |
| 이동 | **도보만** 계산 (대중교통 없음) |

### 1.3 범위

| 포함 | 제외 |
|---|---|
| 지도 탐색, 매물 상세, 생활 프로필, 추천(3요소 점수), 비교 | **정책 자격 판정·대상자 여부·정책 골라 주기** (매칭) |
| 의사결정 분석 (Pareto·Trade-off·민감도·조건 완화·제외 이유) | 정책 지원금을 추천 점수·유지비에 넣는 것 |
| 참고 정보: 주변 실거래 비교, 편의시설, 건축물대장, 계약 전 확인 목록 | 안전 점수 단정 (CCTV 개수 등으로 '안전' 판정) |
| 챗봇: 조건 추출·what-if·되돌리기·장소 조건·자주 가는 곳·분석·**정책 안내** | 광고 매물을 입주 가능으로 확정 표시 |
| 시간표 캡처 OCR (미리보기 → 확인 후 추가) | 과거 실거래를 현재 매물처럼 표시 |
| | **PBO (A/B 선호 학습)** — 에이전트 로드맵에서 제외 (11-10). 하려면 독립 실험으로만 |

**정책 안내 규칙** (11-15 사용자 결정, `CLAUDE.md` 0-7): 공식 데이터의 정책 내용을 출처·기준일·공식 링크와 함께 **설명만** 한다. "대상자입니다 / 받을 수 있습니다" 같은 판정 금지, 자격·신청은 공식 사이트로 안내.

### 1.4 초기 근거 (학생 맞춤 추천 기준, `student-criteria.md`)

처음 정한 9개 후보 기준. 현재 엔진은 이 가운데 '통학'만 점수에 쓰고(3요소), 나머지는 참고 정보·챗봇 조건으로 일부 대체했다.

| # | 기준 | 계산 아이디어 | 지금 상태 |
|---|---|---|---|
| 1 | 주간 총 통학 시간 | 시간표 요일별 건물 × 도보시간 합 | 이동 시간 점수의 '시간표' 부분 |
| 2 | 공강 귀가 가능 | 공강 > 왕복 + 여유 | 없음 (v3 에서만 있었음, 통합안에서 삭제) |
| 3 | 자주 가는 곳 (알바 포함) | 장소별 이동 × 주간 횟수 | 챗봇 '자주 가는 곳' (4.8) |
| 4 | 심야 귀가 | 늦은 일정 + 안전시설 | ⬜ 안전 단정 금지 |
| 5 | 1교시 부담 | 9시 수업 날 출발 시각 | 없음 (v3 에서만) |
| 6 | 오르막 | 고도차 | ⬜ |
| 7 | 생활 편의시설 | 반경 내 시설 | 참고 정보 + 챗봇 장소 조건 |
| 8 | 조용함 vs 번화함 | 술집·유흥 수 | 챗봇 장소 조건(술집·노래방) |
| 9 | 생활 리듬 | 성향 × 영업시간 | ⬜ |

### 1.5 공통 데이터 원칙

- 광고 매물은 `advertised_unverified`. 원본 링크 확인 안내. 거래 불가 확인된 매물은 추천·지도에서 뺀다 (5.6).
- 관리비 `null` ≠ 0 → 유지비 계산 불가 → 추천에서 '정보 없음'으로 제외.
- 경로 실패를 0분·직선거리로 채우지 않는다. 법정동 중심점을 매물 위치로 쓰지 않는다 (좌표 = 지번 대표점).
- 건물 좌표는 카카오 POI 대표 지점 (출입구 아님, `entrance_verified=false`).
- 수집 데이터(`DB/raw`, `DB/staging`, `DB/routes`, `DB/ref`, `DB/export`)·`.env.local`·턴 기록·평가 결과는 **커밋·공개 배포 금지** (11-09: 로컬 PoC 전용 최대 수집).
- 화면 문장에 주소 대신 매물 ID 가 나오는 곳은 그대로 둔다 (11-15 사용자 결정, 저작권).

---

## 2. 현재 구조

### 2.1 디렉터리

```text
home_poc-integrated/
├── BE/            FastAPI (app/), 수집 파이프라인 (pipeline/), 테스트 (tests/), 평가 (eval/, scripts/)
├── FE/            React + TypeScript + Vite + Kakao Maps JS SDK
├── DB/            poc_ewha.csv(배포용 10건), raw/ staging/ routes/ ref/ export/ (로컬 전용), reference/ (정책 임시 목록)
├── docs/          문서 (이 파일)
└── render.yaml    Render Blueprint (BE)
```

AI 코드는 별도 `AI/` 가 아니라 `BE/app/agent/` 에 있다.

### 2.2 BE 주요 모듈 (`BE/app/`)

| 모듈 | 역할 |
|---|---|
| `scoring.py`, `scoring_config.py` | **추천 엔진(통합안)** `recommend()` 와 모든 설정값 (`ScoringConfig`, `DEPARTMENT_BUILDINGS`) |
| `exclusions.py` | 챗봇 제외 조건: 확인 계획(`ExclusionPlan`) 형식과 도구 실행(`ExclusionRunner`) |
| `place_search.py` | 장소 조건을 상가정보로 확인 (`LocalStoreSearcher`, 카카오 호출 0) |
| `building_walks.py` | 건물 기준표(DB/ref) + 고른 건물의 빈 도보시간을 그때 계산 ('B 방식', 11-16) |
| `frequent_places.py` | 자주 가는 곳: 카카오 장소 검색 + 상위 매물 도보 경로 (11-16, 챗봇 전용) |
| `buildings.py` | 건물 이름 표준화·줄임말 표(`ALIASES`, 초안·팀 확인 필요) |
| `data.py`, `models.py`, `config.py` | 매물·도보시간 로딩 (`DATA_CSV_PATH`, `WALK_TABLE_PATH`), 설정 읽기 (프로세스 환경 → `BE/.env` → `BE/.env.local`) |
| `listing_status.py` | 거래 가능 재확인 결과 적용 (unavailable 제외) |
| `decision/` (`engine, pareto, sensitivity, relaxation`) | 의사결정 분석 (엔진 결과·요청 불변) |
| `market.py`, `amenities.py`, `building_register.py`, `poi.py`, `publicdata.py` | 실거래 비교, 편의시설, 건축물대장, 지하철·버스·공원 거리, data.go.kr 공통 |
| `policy.py` | 정책 안내 (검색·비교·신청·지역·계약 점검 목록, 판정 없음) |
| `timetable_ocr.py` | 시간표 캡처 OCR (OpenCV + EasyOCR) |
| `agent/` (`schemas, tools, llm, graph, spec, evaluate`) | LangGraph 단일 에이전트 |
| `tools/kakao.py` | 카카오 REST 클라이언트 |

### 2.3 API

| 메서드 · 경로 | 설명 |
|---|---|
| `GET /health` | `{"status":"ok"}` |
| `GET /properties`, `GET /properties/{id}` | 매물 (목록은 거래 불가 제외, 상세는 포함하고 상태 표시) |
| `POST /recommend` | 통합안 추천 (3장). 고른 건물의 빈 도보시간은 그때 계산 |
| `GET /recommend/buildings?university=` | 건물 목록: `{name, covered, total, aliases, building_type, can_compute}` |
| `POST /analysis/pareto` · `/tradeoff` · `/sensitivity` · `/relaxation` | 의사결정 분석. 본문 `{request: RecommendRequest, ...}` |
| `GET /market/compare/{id}` | 주변 전월세 실거래 비교 |
| `GET /market/amenities/{id}` | 주변 생활 편의시설 |
| `GET /market/building/{id}` | 건축물대장 표제부 |
| `GET /market/contract-check/{id}` | 계약 전 확인 목록 + 이 매물의 사실 (판정 없음) |
| `POST /agent/chat` | 챗봇 (4장) |
| `POST /agent/timetable-image` | 시간표 캡처 → 미리보기 (**OCR 로만**, 11-16) |
| `POST /timetable/ocr` | 같은 OCR 미리보기 |

삭제됨: v3 `GET /recommend/categories`, `POST /recommend/v2`.

### 2.4 환경 변수 (`BE/.env.example` 에는 이름만, 값은 `BE/.env.local`)

| 묶음 | 변수 |
|---|---|
| 기본 | `CORS_ORIGINS`, `DATA_CSV_PATH`, `WALK_TABLE_PATH` (쉼표로 여러 파일, 상대 경로는 `BE/` 기준, ID 중복이면 에러), `LISTING_STATUS_PATH` (`off` 로 끔) |
| LLM | `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDERS`, `LLM_BUSY_COOLDOWN_S`, `AGENT_LLM_EXPLAIN`, `AGENT_TRACE_PATH` |
| EXAONE | `EXAONE_API_KEY`, `EXAONE_BASE_URL`, `EXAONE_MODEL`, `EXAONE_FALLBACK_MODELS`, `EXAONE_REASONING_EFFORT`, `EXAONE_JSON_MODE` |
| Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_FALLBACK_MODELS` |
| (예전 실험) | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `LLM_MODEL` (확인 필요: 현재 사용처) |
| 카카오 | `KAKAO_REST_API_KEY`, `PLACE_SEARCH` (`local` 기본 / `kakao`), `FREQUENT_PLACES_KAKAO`, `FREQUENT_DAILY_CALL_LIMIT` (+ 코드에만: `FREQUENT_WALK_CACHE_PATH`, `FREQUENT_CALL_LOG_PATH`, `BUILDING_WALK_KAKAO`, `BUILDING_WALK_CACHE_PATH`, `BUILDING_CALL_LOG_PATH`, `BUILDING_DAILY_CALL_LIMIT`) |
| 공공데이터 | `DATA_GO_KR_SERVICE_KEY` (Encoding 키 그대로 넣어도 코드가 `unquote`), `MOIS_CONVENIENCE_STORE_API_KEY`, `MARKET_TX_PATH`, `STORES_PATH`, `BUILDING_TITLES_PATH`, `POI_PATH` |
| 정책 | `YOUTHCENTER_API_KEY`, `YOUTHCENTER_API_URL`, `YOUTHCENTER_KEY_PARAM`, `YOUTHCENTER_PAGE_PARAM`, `YOUTHCENTER_SIZE_PARAM`, `YOUTHCENTER_EXTRA_PARAMS`, `POLICIES_PATH` |
| 기타 | `JIBSA_PYTHON` |

FE: `FE/.env.local` 의 `VITE_API_URL`(추천에 필수, 비우면 내장 이대 10건만), `VITE_KAKAO_MAP_KEY`(없으면 지도 자리에 안내만).

### 2.5 전체 흐름

```text
[수집] jibsa(직방) → DB/raw/jibsa/<ts>/ → jibsa_adapter → DB/staging/*_listings_*.csv
[경로] route_batch (카카오 주소 검색 + 도보 경로) → DB/routes/ → build_candidates → *_candidates_*.csv
       build_walk_table → *walk_times*.csv  (+ 요청 때 building_walks 캐시)
[참고] collect_rent / collect_stores / collect_building / collect_poi → DB/staging/
[BE]   DATA_CSV_PATH · WALK_TABLE_PATH → app.scoring.recommend / app.decision / app.market / app.agent
[FE]   VITE_API_URL → 탐색 · 상세 · 생활 프로필 · 매물 비교 · 챗봇 패널
```

---

## 3. 추천 엔진 (통합안, `app.scoring.recommend`)

11-13 회의 확정안(+ 11-13 밤 개정: 공통 규칙·교환비). 계산은 전부 코드, LLM 없음. 숫자는 전부 `ScoringConfig` 초기값이며 테스트 케이스로 조정한다.

### 3.1 입력 (`POST /recommend`)

```json
{
  "university": "이화여자대학교",
  "department": "직접 선택",
  "department_buildings": ["ECC", "공과대학"],
  "grade": 1,
  "priorities": ["travel", "cost"],
  "budget_manwon": 75,
  "deposit_budget_manwon": 1000,
  "desired_commute_min": 15,
  "desired_area_m2": 20,
  "larger_area": "neutral",
  "timetable": [{"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"}],
  "exclusions": []
}
```

| 필드 | 규칙 |
|---|---|
| `university` | 이 대학 매물만 추천 |
| `department`, `grade` | 필수 필드지만 **점수에 안 씀**. FE 는 학과·학년 칸을 없앴고(11-16) `department="직접 선택"`(학과 미입력 시), `grade=1` 로 보낸다 |
| `department_buildings` | 수업 듣는 건물. `null` 이면 이대 학과 → 건물 예시표(`DEPARTMENT_BUILDINGS`, 3개 학과, 예시값·팀 확인 필요) 사용 |
| `priorities` | `travel` / `cost` / `area` 중 서로 다른 2개 (3순위 자동). v2 의 `transit` 삭제 |
| `budget_manwon` | 유지비(월세 + 관리비) 상한, 필수 |
| `deposit_budget_manwon` | 선택. 없으면 보증금 필터 생략 |
| `desired_commute_min`, `desired_area_m2` | 필수, > 0. 각 요소의 기준(50점) |
| `larger_area` | `like` / `neutral`(기본) / `dislike` |
| `exclusions` | 챗봇이 만든 확인 계획 (4.7). FE 는 저장된 프로필의 것을 그대로 보냄 |

### 3.2 필터 (매물마다 순서대로, 처음 걸린 사유 하나만 남김)

| 순서 | 조건 | `stage` |
|---|---|---|
| 0 | 요청 대학 매물만 | (목록·제외 목록에 넣지 않음) |
| 1 | 매물 ID 중복 | `duplicate` |
| 2 | 유지비 > 상한 + **10만 원** (`monthly_budget_slack_manwon`) | `budget` ("유지비가 상한보다 월 12만 원 많아서 제외") |
| 3 | 보증금 > 상한 × **1.2** (`deposit_budget_slack_ratio=0.2`, 입력 시만) | `deposit` |
| 4 | 챗봇 제외 조건 | `exclusion` (+ `condition`) |
| 5 | 월세·관리비 또는 면적 정보 없음 | `missing` |
| 6 | 고른 건물 중 도보시간 없는 건물 | `missing` ("도보시간 정보 없음: 공과대학") |

- 유지비가 상한 ~ 상한 + 10만 원 사이면 **남기고** `over_budget: true` → '예산 초과' 표시.
- 관리비 `null` 매물은 2단계를 건너뛰고 5단계에서 제외.
- 거래 불가 매물은 엔진 전에 `load_properties()` 에서 빠진다 (5.6).
- 그 대학 건물 목록에 없는 건물 이름이 오면 **422** (`UnknownBuildingError`).

### 3.3 요소 점수 — 공통 규칙 (0~100, 절대 점수)

```text
점수 = 50 + 5 × (기준보다 좋은 쪽 단위 수)     기준보다 좋을 때
     = 50 − 10 × (기준보다 나쁜 쪽 단위 수)    기준보다 나쁠 때
     → 0~100 으로 자름
단위(교환비): 편도 10분 = 월 5만 원 = 2㎡
```

| 요소 | 기준 | 좋은 쪽 (+5/단위) | 나쁜 쪽 (−10/단위) |
|---|---|---|---|
| 이동 시간 (`travel`) | 희망 통학 시간 (편도) | 더 가까움 (10분 단위) | 더 멂 (10분 단위) |
| 예산 (`cost`) | 유지비 상한 | 더 쌈 (5만 원 단위) | 상한 초과 (여유 구간, 5만 원 단위) |
| 면적 (`area`) | 희망 면적 | 넓은 쪽: 좋음 +5/2㎡ · 상관없음 50점 유지 · 싫음 −2.5/2㎡ | 더 좁음 (2㎡ 단위) |

**교환비 근거** (11-13, 4개 대학 매물 1,175개 회귀, 대학·유형·보증금 통제): 도보 1분 ≈ 월 0.43만 원 (90% CI 0.30~0.56), 면적 1㎡ ≈ 월 2.4만 원 (1.95~2.86). 도보 값은 고려대에서만 통계적으로 뚜렷했다. 시장 가격이라 학생이 느끼는 통학 무게보다 작을 수 있음 → 통학을 중시하면 1순위(0.55)로 반영. 데이터·테스트 결과로 다시 정할 수 있다.

**예시** (희망 15분, 상한 75만, 희망 20㎡)

| 이동 | 0분 | 5분 | **15분** | 25분 | 35분 | 65분+ |
|---|---|---|---|---|---|---|
| 점수 | 57.5 | 55 | **50** | 40 | 30 | 0 |

| 유지비 | ≤25만 | 45만 | 65만 | **75만** | 80만 | 85만 |
|---|---|---|---|---|---|---|
| 점수 | 100 | 80 | 60 | **50** | 40 | 30 |

| 면적 | ≤10㎡ | 16 | 18 | **20** | 24 | 30 | 40 |
|---|---|---|---|---|---|---|---|
| 좋음 | 0 | 30 | 40 | **50** | 60 | 75 | 100 |
| 상관없음 | 0 | 30 | 40 | **50** | 50 | 50 | 50 |
| 싫음 | 0 | 30 | 40 | **50** | 45 | 37.5 | 25 |

### 3.4 이동 시간 점수 구성

```text
이동 시간 점수 = 0.4 × 학과 건물 + 0.4 × 자주 가는 곳 + 0.2 × 시간표   (각 부분에 3.3 규칙 적용 후 합)
```

| 부분 | 편도 평균 | 비고 |
|---|---|---|
| 학과 건물 (`department`) | 매물 ↔ 고른 건물 (여러 개면 평균) | 항상 |
| 자주 가는 곳 (`frequent`) | 장소별 도보 평균 | **챗봇이 상위 매물만 계산해 넘길 때만** (4.8). FE `/recommend` 에는 없음 |
| 시간표 (`timetable`) | 요일별 집 → 첫 수업, 마지막 수업 → 집 | 입력했을 때만 |

입력·데이터가 없는 부분의 비중은 나머지에 비율대로 나눈다. 항상 붙는 notes: "자주 가는 곳은 데이터 준비 중이라 …"(frequent 없을 때), "시간표를 입력하지 않아 이동 시간은 학과 건물만으로 계산했습니다."(시간표 없을 때), "조건에 맞는 매물이 N개뿐입니다."(1~2개).

### 3.5 종합·정렬

- 종합 = 0.55 × 1순위 + 0.30 × 2순위 + 0.15 × 3순위.
- 통과 매물 중 하나라도 어느 요소 점수가 없으면 그 요소 가중치를 나머지에 비율대로 나누고 notes 에 안내.
- 정렬: 점수 높은 순 → 유지비 낮은 순 → 매물 ID 순.
- 반올림은 응답에만 (점수 2자리, 분 1자리, 금액·면적 2자리).

### 3.6 결과 (`RecommendResult`)

| 필드 | 내용 |
|---|---|
| `priorities`, `weights` | 1·2·3순위와 실제 가중치 |
| `ranked[]` | `rank, score, factor_scores, contributions, travel_parts, vs_target(분·만원·㎡ 차이), monthly_cost_manwon, deposit_manwon, area_m2, over_budget` |
| `not_recommended[]` | 다른 순위 조합(6가지) 중 어디서든 상위 3위인데 지금은 3위 밖, 최대 3개. 1위와 가중 점수 차이가 가장 큰 요소를 사유로 (`top_in, weak, strong, message`) |
| `excluded[]` | `property_id, reason, stage, condition` |
| `exclusion_counts` | 챗봇 조건별 제외 수 |
| `assumptions` | 여유 금액, 기준값·넓은 면적 선택, 가중치, 적용된 챗봇 조건, 예시표 출처 등 |
| `notes[]` | 계산 안내 (건물 이름 바꿔 읽음: "건물 'X'을(를) 'Y'(으)로 읽었습니다." 포함) |

화면에는 점수 대신 **기준 대비 값**("희망보다 5분 가까움", "예산보다 8만 원 쌈")을 권장 (기준 맞춤 = 50점, 이동 최고 57.5점이라 점수 자체는 낮아 보임).

### 3.7 도보시간 출처와 그때 계산 (B 방식, 11-16)

- 기본: 매물 CSV 의 `spot1~4`(대학별 기본 강의동 4곳) + `WALK_TABLE_PATH` 도보시간 표(매물 × 건물, 긴 형식).
- 건물 목록: 위 표 + `DB/ref/*_buildings.csv` (출입구·정문 행 제외). 표준 이름 = `csv_spot_name` 이 있으면 그것, 없으면 카카오 장소 이름에서 대학명을 뗀 것.
- 고른 건물에 도보시간이 없는 매물은, **예산·보증금 필터를 통과한 매물에 한해** 요청 때 카카오 도보 경로로 계산하고 `DB/staging/building_walk_cache.csv` 에 저장해 다시 쓴다. 요청 1번 최대 **150회**, 하루 **700회** (`BUILDING_DAILY_CALL_LIMIT`). 한도를 넘으면 계산하지 않고 그 매물은 '도보시간 정보 없음'으로 빠지며 notes 에 안내.
- FE 는 `can_compute` 이거나 모든 매물에 도보시간이 있는 건물만 선택지로 보여 준다.

### 3.8 챗봇 제외 조건 (확인 계획)

LLM 은 매물을 직접 판단하지 않고, 서버가 만든 **확인 계획**을 코드가 매물마다 실행한다. 같은 계획이면 결과가 항상 같다.

| 도구 | 하는 일 | 데이터 |
|---|---|---|
| `nearby_count` | 반경 N m 안 장소 개수 | 기본 상가정보(`PLACE_SEARCH=local`), `kakao` 면 카카오 장소 검색 |
| `nearest_distance` | 가장 가까운 장소까지 거리 (최대 1,000m) | 같음 |
| `listing_field` | 매물 칸 (가격·면적·층 종류·층·방 구조·유형 등) | 매물 CSV / 통합 데이터셋 (아래 미결) |
| `building_field` | 건축물대장 칸 (연식·용도·층수·승강기·주차·세대) | 통합 데이터셋 |
| `amenity` | 주변 시설 최근접 거리·500m 개수 | 통합 데이터셋 (공공데이터로 미리 계산) |

```json
{"source_text": "밤에 시끄러우면 잠을 못 자요",
 "conditions": [{"tool": "nearby_count", "label": "술집", "keywords": ["술집","호프","클럽"],
                 "radius_m": 150, "exclude_if": {"op": ">", "value": 2}}],
 "unsupported": [], "confirm_message": "…"}
```

- 확인할 수 없는 기호는 추측해서 빼지 않고 `unsupported`, 현장에서 봐야 하는 것(방음·곰팡이·채광·수압)은 `onsite_checks` 로 안내.
- 매물 좌표가 없으면 장소 도구를 실행하지 않고 빼지 않는다 → notes 에 개수.
- 추천 한 번 카카오 호출 상한 500 (`kakao_call_limit`). 로컬 검색기(`unlimited`)에는 상한 미적용. 카카오 결과는 프로세스 메모리 캐시만 (파일 저장 금지).
- ⚠️ 미결: `listing_field`·`building_field`·`amenity` 는 **통합 매물 CSV 칸**을 읽는다 → 서비스 매물 CSV(`DATA_CSV_PATH`)를 통합 데이터셋으로 바꿀지 결정 필요 (확인 필요).

### 3.9 이전 설계 (기록용)

| 버전 | 기간 | 내용 | 지금 |
|---|---|---|---|
| v1 | 11-06 | min-max 정규화 + 고정 가중합 0.40/0.35/0.25 | 폐기 |
| v2 (`recommend-score-design.md`) | 11-07 | 이동시간·교통 편의성(역 도보 T=5,h=5)·주거비. 시간 점수 = 희망 85점 + 반감기 지수 감소. 11-08 에 예산 여유 0·주거비 상대 비교로 변경 | 통합안의 바탕. 요소 점수 함수·`transit` 은 대체됨 |
| v3 (`app/categories.py`) | 11-08~10 | 카테고리 9개 중 1~3개 순서 선택, 가격은 필터만(여유 0) | **삭제** (통합안 브랜치 b66244a) |
| 통합안 | 11-13~ | 3장 | 현재 |

v2 에서 남은 참고: 대중교통 추정 '방법 A'(매물→역 도보 + 역간 소요 + 환승 5분 + 대기 3분 + 역→장소 도보) 설계와 ODsay 미사용 이유(무료 하루 30건, 장소×매물 호출, 유료 건당 25원, 저장 사전 협의) — 대중교통을 넣게 되면 다시 검토. 순위 기반 가중치(직접 가중치보다 정확)·LLM 에 가중치 숫자를 만들게 하지 않음은 Eichelbeck et al. ICLR 2026 근거.

---

## 4. 챗봇 에이전트 (LangGraph 단일 에이전트)

### 4.1 원칙

| 원칙 | 구현 |
|---|---|
| 점수·순위는 엔진이 | `app.scoring.recommend` 직접 호출 (자기 HTTP 호출 없음). `POST /recommend` 와 순위·점수 일치 테스트 |
| LLM 은 해석과 설명만 | (1) 메시지 → 의도 + 바꿀 조건 (`TurnExtraction` 구조화 출력) (2) 서버가 만든 사실 JSON → 설명 |
| 도구·매개변수는 서버가 | 의도를 정해진 노드로 연결하고 값은 서버가 검증 (대학 별칭, 건물 표준화, 금액 범위, 모르는 건물 미반영) |
| 입력되지 않은 값은 만들지 않음 | 필수 조건이 없으면 묻는다 |
| 기존 기능 보호 | LLM 이 없거나 실패해도 `/recommend` 등은 그대로. `/agent/chat` 은 500 대신 `status` 로 알림 |
| 단일 에이전트 | 멀티 에이전트·PBO 쓰지 않음 |

### 4.2 그래프

```text
START → extract (LLM) → update_profile (서버 검증)
  ├─ confirm (interrupt: 학교 변경·전체 되돌리기)
  ├─ policy → respond
  ├─ compare_previous → validate
  ├─ respond (explain · smalltalk · 오류)
  └─ check_required ─(부족)→ respond
                     └→ recommend | what_if | analyze → validate → respond (설명 + 숫자 검증) → END
```

| 노드 | 하는 일 | LLM |
|---|---|---|
| `extract` | 턴 초기화. FE 프로필이 오면 현재 조건으로. 맥락(현재·이전 조건, 지난 TOP 3, 최근 대화 6개, 추천 명세)과 함께 추출 | ✅ |
| `update_profile` | `recommend/update/analyze`: 조건 반영(장소 조건은 바로 적용, 4.7). `what_if`: 가정 조건만 따로. `revert`: 직전 조건 전체 복원. 계산 불가 요청은 notes | – |
| `confirm` | `pending_commit` 이면 LangGraph `interrupt` 로 네/아니요 대기 | – |
| `check_required` | 필수 조건 확인, 매물 없는 대학 차단 | – |
| `recommend` / `what_if` / `analyze` / `compare_previous` | 엔진·분석 도구 실행, 조건이 바뀌면 이전 결과와 비교 | – |
| `policy` | 정책 안내 (프로필 필수 조건 없이 바로) | – |
| `validate` | TOP 매물 ID 존재, **1위부터 오름차순·엔진 순위 일치** (같은 집 묶기로 rank 가 1·2·4 처럼 건너뛸 수 있음) | – |
| `respond` | 사실 → 설명, 숫자 검증, 실패 시 사실 문장, 턴 기록 | ✅ (설정에 따라) |

### 4.3 의도 · 분석 · 정책 종류

| 의도 | 예 | 저장 |
|---|---|---|
| `recommend` | "이대 70만 원 이하로 추천해 줘" | 조건 있으면 저장 |
| `update` | "예산을 80으로", "공대 수업이 많아" | 저장 |
| `what_if` | "10만 원 올리면?" | **저장 안 함** (가정 결과와 비교) |
| `revert` | "이전 조건으로 전부 되돌려 줘" | 직전 조건 전체 복원. 일부 복원은 `update` |
| `compare_previous` | "아까 결과랑 비교해 줘" | – |
| `analyze` | "순위가 얼마나 흔들려?" | 같이 온 조건은 저장 |
| `policy` | "청년 월세 지원 알려 줘" | 프로필 안 바꿈 |
| `explain` | "2등 매물은 왜?" (`property_rank`) | – |
| `smalltalk` | "안녕" | – |

- 분석 종류(`analysis`): `pareto · tradeoff · sensitivity · relaxation · market · amenities · building · excluded`. 모두 참고 분석, 순위 안 바뀜. 매물 대상 분석은 지난 추천의 N위(기본 1위).
- 정책 종류(`policy_kind`): `info · eligibility · compare · apply · contract_safety` (4.9).
- `ChatRequest.mode`: `recommend` | `policy` | 없음(자동 판별). FE 챗봇 패널에 **'매물 추천' / '정책 안내' 모드 버튼**이 있고 모드마다 session_id·대화를 따로 둔다. (11-15 기록의 "모드 버튼 없이 같은 챗봇"을 대체. 11-16 사용자 확인: 모드 버튼이 맞음)

### 4.4 프로필 (`AgentProfile`, FE 생활 프로필과 1:1)

`university, budget_manwon, deposit_budget_manwon, department, department_buildings, grade, priorities(1·2순위), desired_commute_min, desired_area_m2, larger_area, timetable, exclusions, frequent_places(최대 3)`

- 추출 전용 필드: `budget_delta_manwon`(증감), `area_min_m2`(면적 하한 → 엔진 제외 조건 `listing_field area_m2 < N`, 이름 '면적 N㎡ 이상'), `place_prefs`, `remove_exclusions`, `frequent_places`, `remove_frequent_places`. 평 → ㎡ 환산("5평" → 16.5).
- **필수**(`tools.missing_fields`): 학교 · 유지비 상한 · 1·2순위 · 희망 통학 시간 · 희망 면적 · 학과 건물(이대 예시표에 있는 학과면 생략). 학년은 묻지 않음(엔진 요청엔 1).
- 학교가 바뀌면 이전 학교 건물·시간표를 비움. 금액 0 이하 거부. 건물별 방문 횟수는 저장 칸이 없어 `unsupported` 안내(시간표를 넣으면 요일별 반영).

### 4.5 확인 단계 (human-in-the-loop)

| 묻는 경우 | 이유 |
|---|---|
| 학교가 바뀌면서 그 학교 기준 건물·시간표가 지워질 때 | 되돌리기 어려운 입력 손실 |
| 전체 되돌리기로 두 항목 이상 바뀔 때 | 여러 조건이 한꺼번에 바뀜 |

- 장소 제외 조건은 **묻지 않고 바로 적용**하고 답변 맨 앞에 '바뀐 것'(빠진 수·남은 수·되돌리는 법). 적용하면 0개가 남을 때만 적용하지 않고 되묻는다 (11-15 팀 결정, 커밋 `ac762c7`).
- 답 읽기는 규칙 기반 `yes_no` (LLM 안 씀). 답이 아닌 메시지가 오면 변경을 취소하고 새 요청으로 처리 (`run_turn`).
- API: `status="confirm"`, `pending_change`(필드 → before/after). FE 는 "네, 바꿀게요 / 아니요" 버튼.

### 4.6 도구 (`agent/tools.py`)

| 도구 | 함수 | 검증·비고 |
|---|---|---|
| Recommendation | `run_recommendation` | 필수 조건, 대학별 매물만. **같은 집일 수 있는 광고는 가장 앞선 것 하나만 TOP 에**, 나머지는 `same_house_ids` |
| Analysis | `app.decision`, `app.market`, `app.amenities`, `building_for`, `excluded_analysis` | 참고 분석 |
| Property | `property_info` | 실제 매물 ID 만 |
| Building | `usable_buildings`, `resolve_buildings` | 줄임말·표기 차이 표준화, 모르는 건물은 반영 않고 고를 수 있는 건물 안내 |
| Comparison | `compare` | TOP 3 들어온·빠진 매물, 후보 수, 순위·점수 변화, 조건 차이 |
| 조건 반영 | `apply_changes`, `build_exclusion_plan`, `area_min_plan`, `add_frequent_place` | 4.4·4.7·4.8 |
| Policy | `app.policy` | 4.9 |

**추천 명세** (`agent/spec.py`): 코드 설정값(`ScoringConfig`)과 실제 데이터(대학별 매물 수·건물)로 짧은 명세를 만들어 추출(`service_spec`)·설명(`how_it_works`)에 넣는다. 문서(md)를 프롬프트에 붙이지 않는다 (코드와 어긋나고, 길면 lite 모델 정확도 하락).

### 4.7 장소 조건 (상가정보로 확인)

- LLM 은 `place_prefs`(장소·avoid/near·거리·허용 개수)만 뽑고, 확인 계획(도구·반경·기준)은 서버가 만든다 (`tools.build_exclusion_plan`).
- 기본 반경: 피하기 100m, 가까이 300m (⚠️ 초기값). 개수를 말하지 않으면 1곳 이상이면 제외.
- 확인 가능 장소: 편의점·슈퍼(마트)·세탁소·빨래방·약국·병원(의원)·술집(주점·호프)·노래방·**헬스장**. 그 밖(도서관·카페 등)은 조건에 넣지 않고 안내.
- 해제(`remove_exclusions`)는 바로. what-if 로 가정 비교 가능. FE 는 프로필에 `exclusions` 저장 → `/recommend` 에 그대로, 추천 조건 요약에 '장소 조건' 칩(제외 수, 해제 버튼).
- 실측 (이대·70만·통학 1순위): 기본 233개 → 술집 100m 안 없음 111개 (129개 제외, 0.6초) · 편의점 100m 안 187개 · 노래방 200m 안 2곳 이상 제외 178개 (11-15, 매물 정리 전 데이터).

### 4.8 자주 가는 곳 (11-16, 챗봇 전용)

| 항목 | 값 |
|---|---|
| 장소 찾기 | 카카오 키워드 검색, 학교 중심 반경 **3km** 안 1회. 첫 결과 이름이 사용자 표현을 담으면 확정(이름·주소 보여 줌), 아니면 후보를 보여 주고 다시 묻는다. 반경 안에 없으면 반경 없이 1회 더 찾아 '범위 밖'/'못 찾음' 구분 |
| 도보시간 | 1차 순위 **상위 20개** 매물만 카카오 도보 경로 (장소 1곳에 최대 20회). 매물 ID × 장소 ID 로 `DB/staging/frequent_walk_cache.csv` 에 저장(실패도 저장) |
| 반영 | 엔진 `frequent` / `frequent_rerank`: 상위 20개가 앞자리를 그대로 차지하고 그 안에서만 순서가 바뀜. 나머지는 1차 점수·순서 그대로 |
| 한도 | 이 기능 하루 **200회** (`FREQUENT_DAILY_CALL_LIMIT`, 검색 + 경로 합계), 저장 최대 3곳, 방문 횟수 말 안 하면 주 1회 |
| 실패 | 실패·좌표 없음·상한 초과는 None (0분·직선거리 대체 안 함) |
| FE | 생활 프로필 '자주 가는 곳' 섹션은 화면에서 뺐고(저장된 `places` 는 보존), 챗봇이 정한 `frequentPlaces` 를 프로필에 저장. 채팅 카드에 장소·상위 N개만 계산·미확인 수 표시 |

⚠️ 카카오 도보 경로 하루 한도 1,000회를 수집(`route_batch`, 하루 960회 계획)·건물 그때 계산(700)·자주 가는 곳(200)이 나눠 쓴다. 코드 주석은 "700 + 200, 합계 960 이하"로 적었지만 같은 날 수집 배치와 겹치면 넘을 수 있다 (11-16 사용자: 인지함. **지금은 건물 도보시간을 모두 반영하는 것이 우선**).

### 4.9 정책 안내 (`app/policy.py`, 의도 `policy`)

| 종류 | 처리 |
|---|---|
| `info` | 낱말·유의어 검색, 지역으로만 거름 |
| `eligibility` ("받을 수 있는 정책 전부") | **판정하지 않는다**는 안내 + 공고 기준(연령·소득 표기) + 공식 링크. 나이·소득 저장 안 함 |
| `compare` (`policy_names`) | 이름마다 하나씩 나란히, **유불리 판단 안 함**, 못 찾은 이름은 공식 사이트 안내 |
| `apply` | 신청 방법·서류·심사 표기 그대로, 없으면 공식 페이지 |
| `contract_safety` | 계약 전·당일·입주 후 13항목 + 공식 링크(인터넷등기소·세움터·실거래가 공개시스템·정부24·HUG). 정책 데이터 없이 동작. **서비스가 정리한 일반 목록 — 공식 문서 원문 대조 미검증, 법률 자문 아님** 표시 |
| 지역 (`policy_regions`) | 서울 자치구 25곳은 '서울'을 함께 찾음. 구 단위 정책은 구청 누리집 안내 |

- 데이터 우선순위: `DB/staging/policies_*.json`(온통청년 API 수집) → 없으면 **임시 목록 `DB/reference/policies_curated.json`** → 그것도 없으면 공식 사이트(온통청년·마이홈)만 안내. 출처 표시 `policy_source`.
- 임시 목록 (2026-11-16 공식 페이지 직접 확인, 6개): 청년월세 지원사업, 서울시 청년월세지원, 청년전용 버팀목전세자금, 청년전용 보증부월세대출, 행복주택, HUG 전세보증금반환보증. 페이지에 없는 값은 null, 항목마다 `ref_url`·`fetched_at`·`status_note`. 2026년 청년월세 두 사업은 '접수 끝남'. 제외: 중소기업취업청년 전월세대출(메뉴에서 사라짐, 종료 공지 미확인), LH 청년 매입·전세임대(JS 페이지라 확인 불가). 한계: 손으로 옮긴 것이라 공고가 바뀌면 낡음.
- 데이터가 없을 때는 LLM 없이 고정 문장 (`template_no_policy_data`) — LLM 이 '일시적 지연'으로 바꿔 말하던 문제 때문.
- 설명 규칙에 "대상입니다/받을 수 있습니다" 금지. 매물 상세의 계약 전 확인 카드는 `GET /market/contract-check/{id}` (매물 사실을 붙이되 판정 없음).

### 4.10 LLM 공급자와 오류 처리 (`agent/llm.py`)

**순서 (11-16 사용자 결정)**: **Gemini 먼저 → 한도·혼잡·토큰 문제면 EXAONE**. `llm_from_env()` 기본값: `LLM_PROVIDER` 미설정 → `gemini`, `LLM_FALLBACK_PROVIDERS` 미설정 → `exaone` (빈 값으로 두면 대체 없음). 대체 공급자의 키·모델이 없으면 빼고 쓴다. 현재 `BE/.env.local`: `LLM_PROVIDER=gemini`, `LLM_FALLBACK_PROVIDERS=exaone`.

| 항목 | Gemini (`GeminiLLM`) | EXAONE (`ExaoneLLM`) |
|---|---|---|
| 연결 | `langchain-google-genai` `with_structured_output` (json_schema) | OpenAI 호환 chat completions (`openai` 패키지). 구조화 출력 `EXAONE_JSON_MODE=prompt`(기본, 스키마를 프롬프트에) / `json_schema`. 어느 쪽이든 pydantic 재검증. `<thought>` 등 추론 태그·코드 블록 제거. `changes: null` 을 빈 값으로 받음 |
| 모델 (11-15~16 설정) | 주 `gemini-3.1-flash-lite` + 대체 모델 (`GEMINI_FALLBACK_MODELS`, 실제 설정에서 3개 모델로 체인 확인). 모델 ID 는 계정 목록에서 확인해 정함 (추측 하드코딩 금지) | `EXAONE_BASE_URL=https://api.exaone.ai/v1`, `EXAONE_MODEL=k-exaone_v2.5`, `reasoning_effort=low` |
| 재시도 | 호출 20초, 혼잡·시간 초과 시 같은 모델 2회 더 (1.5초·3초), 전체 45초 | 재시도 4회 (2·4·8·16초), 호출 40초, 전체 50초 (시간 제한을 남은 시간으로 줄임) |
| 이미지 | (쓰지 않음, 4.11) | 이미지 입력 없음 |
| 계정 한도 | 분당 한도 429 가 자주 남 (11-15 평가) | 초당 1 · 분당 60 · **하루 100회** (응답 헤더, 실패 요청은 안 셈). 서버 전체 동시 요청 429 `too many concurrent requests(600)` 간헐 → 혼잡으로 분류 |

**`ProviderChain`**

| 오류 종류 (`classify_error`) | 처리 |
|---|---|
| `quota` — 하루 한도, 402, insufficient credit/quota/balance, token limit, tokens per day, daily limit | 그 공급자를 **서버 현지 자정까지** 건너뜀 |
| `busy` — 혼잡·시간 초과, Gemini 429 중 분 단위 표시(`PerMinute`, `retry in`)만 있는 것 | 다음 공급자로, 그 공급자는 **5분**(`LLM_BUSY_COOLDOWN_S`) 뒤로. 다른 공급자가 다 안 되면 미룬 공급자도 시도 |
| `too_long` (context_length_exceeded 등), `not_found`, `auth` | 이번 호출만 다음 공급자 |
| `bad_response` 등 | 전환하지 않고 실패 |

- 전환 전체 시간 상한 `CHAIN_BUDGET_S=60`. 다음 공급자는 남은 시간만 쓰고 **8초 미만이면 시도 안 함**.
- Gemini 429 의 분/하루 구분은 실제 오류 문구로 미확인 (표시가 없으면 `quota`).
- 턴 기록 모델 이름은 `공급자:모델`.

**설명 공급자** `AGENT_LLM_EXPLAIN = on`(기본, 추출과 같은 체인) `| off`(사실 문장만) `| gemini | exaone`(설명만 그 공급자). 11-15 EXAONE 우선 시절 설명이 60초를 넘겨(`reasoning_effort=none` 은 400) `gemini` 로 두었고, 현재 worktree `BE/.env.local` 값은 `on` (Gemini 우선 체인이 설명도 맡음, 11-16 확인). 설명 규칙: "monthly_cost_manwon 은 유지비(월세+관리비)" ('월세'라고 부르던 문제 수정).

**사용자 안내**: 키·모델이 없거나 실패하면 `status = llm_unavailable | llm_error`, 오류 종류별 문구 (혼잡·한도·인증·모델 없음·응답 형식·입력 초과·기타).

### 4.11 시간표 사진 (`POST /agent/timetable-image`, `POST /timetable/ocr`)

| 단계 | 내용 |
|---|---|
| 결정 (11-16) | **OCR 로만 읽는다.** 개인 일정이 담긴 이미지를 외부 LLM 에 보내지 않음 (공급자 순서와 무관). `/agent/timetable-image` 는 `ocr_preview` 를 그대로 부름 |
| 요청 | `{university, image_base64(≤ 약 5MB, base64 7,000,000자), mime_type: png·jpeg·webp}` |
| 읽기 | OpenCV + EasyOCR (`timetable_ocr.py`). 설치는 선택 그룹 `uv sync --group ocr` (torch 포함 1GB+, Render 무료 서버엔 없음) → 없으면 `status=ocr_unavailable` |
| 서버 검증 | 시각 형식·순서, 건물 표기 → 표준 이름 (강의실 번호 떼고 다시 맞춤, 캠/포/학 표기). 못 맞추면 `building: null` + `issue` |
| 응답 | 미리보기 칸, notes, 고를 수 있는 건물. **이미지·결과를 저장하지 않음** |
| FE | 생활 프로필 시간표 섹션 `TimetableImageImport`: 칸별 포함·건물 선택 → "선택한 n칸 추가" → 저장 버튼으로 저장 |
| 검증 | 에브리타임 캡처 1장 14칸 정답 일치, OCR 테스트 11개 (11-15). 1장만 검증 |

캡처 파일(개인 시간표)은 커밋하지 않는다 (`/*.png`, `BE/tests/fixtures/*.png` 무시).

### 4.12 환각 방지와 검증

| 장치 | 내용 |
|---|---|
| 서버 검증 | 4.4 조건 반영 규칙 |
| 결과 검증 노드 | TOP 매물 ID 존재, 1위부터 오름차순, 엔진 순위 일치 |
| 숫자 검증 | `numbers_grounded`: 설명의 모든 숫자가 사실 JSON(명세 포함)에 있어야 함 (0~10 은 순서 표현으로 허용). 아니면 `fallback_text` |
| 대체 문장 | LLM 실패·숫자 불일치·질문 상황·정책 데이터 없음은 사실로 만든 고정 문장 |
| 금지 | 입주 가능·안전 단정, 정책 자격 판정 |

### 4.13 상태 · API · 턴 기록

- 상태: `session_id, messages(최근 20), current_profile, previous_profile, last_recommendation, previous_recommendation` + 턴마다 `intent, extraction, notes, pending_required_fields, scenario_profile, scenario_recommendation, comparison, profile_changed, pending_commit, status, trace_steps …`. 추천 결과는 요약(`RecSummary`)만 둔다.
- 저장: 개발용 `InMemorySaver` (`thread_id = session_id`). **서버 재시작·다중 인스턴스에서 대화가 사라진다** (영속 checkpointer 미구현).
- 요청: `session_id`(1~100자), `message`(1~2000자), `profile`(FE 저장 프로필, 있으면 현재 조건), `mode`.
- 응답: `assistant_message, updated_profile, profile_changed, recommendation_results, comparison_results, analysis_results, policy_results, missing_fields, intent, status(ok·need_info·confirm·llm_unavailable·llm_error·error), trace, pending_change`.
- 턴 기록: `BE/logs/agent_trace.jsonl` (`AGENT_TRACE_PATH`, 빈 값이면 끔) + 응답 `trace`. 필드 `ts, session_id, message, intent, changes, unsupported, status, llm_error_kind, nodes[{node, ms}], tools, ms_total, extract_model, explain_model, explanation_source(llm | fallback_* | template_*), missing_fields, profile_changed, top`. 사용자 메시지가 있어 **커밋 금지**.

### 4.14 테스트 · 평가

| 종류 | 위치 | 내용 |
|---|---|---|
| 단위·흐름 | `BE/tests/test_agent.py`, `test_llm_exaone.py`, `test_policy.py`, `test_timetable_ocr.py`, `test_frequent_places.py`, `test_building_walks.py` 등 | 실제 LLM 없이 `ScriptedLLM`·가짜 client. 엔진 결과 일치, 확인 단계, 장소 조건, 분석 7종+excluded, 공급자 전환·분류, 시간 상한, 시간표 'LLM 호출 없음' |
| 조건 추출 평가 | `BE/eval/agent_cases.jsonl` **52개** (c01~c52) + `BE/app/agent/evaluate.py` + `scripts/eval_agent.py --provider exaone|gemini --models … --sleep N` | 채점: intent·profile·scenario·unchanged·includes·first_priority·timetable_len·property_rank·analysis·places·place_rule·no_places·unsupported·policy_kind·policy_names_min·policy_regions (숫자 ±0.5). 케이스당 LLM 1회. 결과 `BE/eval/results/` (커밋 금지). **실행은 사용자 승인 후** |

**평가 결과 (11-15, 그때 40개 기준)**

| | EXAONE `k-exaone_v2.5` | Gemini `gemini-3.1-flash-lite` |
|---|---|---|
| 최종 | **40/40** (형식 오류 7건 `changes:null` 수정 후 재실행) | **38/40** — c01 '공대생' 학과 건물 미채움, c03 '관리비 포함 한 달 75만원' 놓침 |
| 속도 | 케이스당 약 10~25초 | 1~2초 |
| 비고 | 서버 혼잡 4건 | `--sleep 5` 없이 22개 분당 429 |

- c36 문장을 "술집 근처 매물은 피한다고 치면…" 으로 고침(두 뜻 문제). c34 확인 불가 장소 예시를 헬스장 → 도서관으로 바꿈 (위 결과는 옛 c34 기준).
- c41~c52(정책·예시 프롬프트 10개)는 실제 LLM 으로 **미평가**. 40개·1회 실행이라 2건 차이는 통계적으로 확정 아님.
- (11-10 v3 시절 32개 평가는 필드가 달라 폐기.)

---

## 5. 데이터

### 5.1 수집 파이프라인 (`BE/pipeline/`, `BE/` 에서 `python -m pipeline.<모듈>`, `--univ ewha|korea|sogang|seoultech`)

| 단계 | 모듈 | 규칙 |
|---|---|---|
| 강의동 기준표 | `ewha_buildings`, `campus_buildings` → `DB/ref/<대학>_buildings.csv` | 카카오 장소 이름이 정확히 1건 일치할 때만. 좌표는 POI 대표 지점 |
| 수집 | `jibsa_collect <계획>` → `DB/raw/jibsa/<ts>/` + manifest | 명령 하나씩 순서대로, 실패 재시도 안 함, 덮어쓰기 안 함, 동마다 50건 × 최대 6페이지 |
| 정규화 | `jibsa_adapter` → `staging/*_listings_*.csv` | 원본에 없는 값 안 만듦, `manage_cost` null ≠ 0, 지번 `lot`/동 `dong`, 중복은 '의심' 표시만 |
| 경로 | `route_batch --max-km 2 --max-calls N` → `DB/routes/routes_*.csv`, `DB/raw/kakao/` | 주소가 하나로 확정될 때만, 2km 초과 안 부름, HTTP 에러면 즉시 중단 |
| 후보 | `build_candidates --types 원룸,오피스텔 --deposit-max 3000` | 기본 강의동 4곳 경로가 **모두 OK** 인 매물만. 이대 외 ID 접두어 `ku:` `sg:` `st:` |
| 도보표 | `build_walk_table` | spot1~4 + routes OK. 같은 (매물, 건물)은 최근 것. 같은 지번이면 예전 경로 재사용 |
| 거래 확인 | `check_listing_status` (+ `jibsa_batch_listing`) | 5.6 |
| 정리·재충전 | `prune_unavailable [--dry-run]`, `refill_listings` (prepare → plan → route_batch → merge) | 5.6 |
| 참고 데이터 | `collect_rent`, `collect_stores [--categories gym]`, `collect_building [--retry]`, `collect_poi`, `collect_policies` | 5.4·5.5 |
| 통합·내보내기 | `build_integrated [--export]`, `export_syntitan` | 5.7 |

수집 계획: `v2_zigbang_ewha9`(이대 9개 동, 11-08), `v3_zigbang_ring`(이대 바깥 18개 동, 11-09), `ku1_zigbang`, `sg1_zigbang`, `st1_zigbang`(11-09). 네이버·다방·당근은 주소가 동까지라 경로 계산 불가 → 직방만.

### 5.2 대학별 기본 강의동 (임시, 팀 확인 필요)

| 대학 | 기본 4곳 |
|---|---|
| 이화여대 | ECC, 이화·포스코관, 학관, 공과대학 (+ 추가 8곳: 인문관·교육관·헬렌관·생활환경관·법학관·약학관A동·종합과학관A동·조형예술관 A동) |
| 고려대 | 정경관(카카오 '상남정경관'), LG포스코경영관, 문과대학 서관, 신공학관 |
| 서강대 | 정하상관(J), 김대건관(K), 리치과학관(R), 바오로경영관 |
| 서울과기대 | 창학관, 프론티어관, 미래관, 다빈치관 |

### 5.3 매물 현황 (로컬, 2026-11-16)

| 대학 | 11-14 행 수 | 거래 불가 정리 후 | 재충전 후 (현재) |
|---|---|---|---|
| 이화여대 | 411 | 161 | **396** |
| 고려대 | 588 | 254 | **546** |
| 서강대 | 410 | 172 | **409** |
| 서울과기대 | 105 | 65 | **105** |
| 합계 | 1,514 | 652 | **1,456** |

- 11-16 재충전: 직방 재수집(검색 76회 성공, `DB/raw/jibsa/20261116T005343·005618·005859·005952·010320`) → 어댑터 `jibsa_listings_v4`(이대)·`sg_listings_v2`·`ku_listings_v2`·`st_listings_v2`. 카카오 도보 **956/960회, 전부 OK** (이대 252·과기대 76·서강대 284·고려대 344, 기본 4곳만). 2km 밖 주소(이대 50·고려대 39·서강대 46·과기대 48곳)는 안 부름.
- 새 매물은 원래 후보 CSV·도보시간 표에 **덧붙임** (덧붙이기 전 파일 `staging/refill/*_before_<ts>.csv`).
- 새 매물의 **추가 강의동 8곳 경로는 아직** (같은 주소로 예전에 부른 것만 재사용).
- **학교 건물 전체 채우기 (11-16 사용자 승인)**: 103개 건물(이대 37·고려대 30·서강대 18·과기대 18) 중 매물 전체에 도보시간이 있는 건물은 16개뿐 → `pipeline/fill_buildings.py` 로 빈 (주소 × 건물) 만 하루 960회씩, **이대 → 서강대 → 서울과기대 → 고려대** 순서, 11-17 시작.

  | 대학 | 계획 (`staging/fill/*_plan_20261116T0150*.csv`) | 호출 | 960회 기준 |
  |---|---|---|---|
  | 이화여대 | `ewha_plan_20261116T015004` | 6,722 | 8일 |
  | 서강대 | `sogang_plan_20261116T015005` | 3,570 | 4일 |
  | 서울과기대 | `seoultech_plan_20261116T015007` | 1,134 | 2일 |
  | 고려대 | `korea_plan_20261116T015009` | 7,826 | 9일 |
  | 합계 | | **19,252** | 약 21일 (12월 초) |

  하루 명령 (BE/ 에서): `python -X utf8 -m pipeline.fill_buildings run --univ ewha --plan staging/fill/ewha_plan_20261116T015004.csv --max-calls 960` → 끝나면 `merge --univ ewha --candidates staging/ewha_candidates_v3_student.csv --walk-table staging/walk_times_v2.csv` → BE 재시작(도보시간 표 캐시). 이미 부른 (주소, 건물)은 실패 포함 다시 부르지 않고, HTTP 오류(429)면 멈춘다. 배치 날에는 그때 계산(700)·자주 가는 곳(200)과 한도가 겹쳐 배치가 429 로 일찍 멈출 수 있다 (사용자 인지). 계획의 2km 필터는 학교 건물 전체의 중심 기준.
- 배포본은 이대 PoC 10건(`DB/poc_ewha.csv`)뿐 (7장).
- 품질 기준선 (11-15, 1,514행): 형식 오류 0, 관리비 결측 2.9%, 추천 가능 97.1%(1,470), ID 중복 0, jibsa 중복 의심 374행, **같은 집일 수 있는 광고 907행**.

### 5.4 참고 데이터 (추천 점수에 안 씀)

| 데이터 | 출처 · 파일 | 규모 | 비고 |
|---|---|---|---|
| 전월세 실거래 | 국토부 RTMS 단독/다가구(15126472)·연립다세대(15126473)·오피스텔(15126475), 6개 구 × 2025-11~2026-10 | **85,191건** (단독/다가구 40,658 · 연립다세대 27,183 · 오피스텔 17,350) | `DB/raw/molit/<ts>/` → `rent_tx_<ts>.csv`. 유형별 가장 최근 파일 사용. 단독/다가구·연립 응답에 `sgg_nm` 없음(79.6% 결측, 코드는 있음) |
| 상가(상권)정보 | 구·업종 단위 일괄 (호출 105 + 헬스장 13) | 28,415곳 (13개 구, 기준 2026-09) + 헬스장 1,574곳 | 업종: 편의점 G20405, 슈퍼마켓 G20404, 세탁소 S20901, 셀프 빨래방 S20902, 약국 G21501, 의원 Q102, 주점 I211, 노래방 R10407, 헬스장 R10307. 업종마다 가장 최근 파일. '슈퍼마켓'에 무인 아이스크림·드럭스토어 섞임 → 출처 분류를 따른다고 notes |
| 건축물대장 | 건축HUB `getBrTitleInfo` (15134735) | 고유 지번 620개 → 618회 + 재시도 19회 | 매물 기준 연결 95.6% (1,448/1,514) · 주건축물 여러 개 31(임의로 안 고름) · 대장 없음 27 · 법정동코드 모름(봉원동) 4 · 오류 4. 법정동코드는 상가정보 `ldongCd` 로 표 생성 |
| 지하철역 | 전국도시철도역사정보표준데이터(15013205), 수도권 546행 | | ⚠️ 기준일 2020-12~2021-08 → 이후 개통 역 누락 가능. 다른 시도 역이 서울 좌표로 들어간 오류 제외, 같은 이름도 300m 넘게 떨어지면 다른 역 |
| 버스정류장 | 국토부 전국 버스정류장 위치(15067528, 2026-11-09) | 수도권 65,162곳 | 길 양쪽 따로 셈 |
| 공원 | 전국도시공원정보표준데이터(15012890) | 수도권 7,218곳 | 대표점 좌표 |
| 행정안전부 편의점 (15101930) | | | ⛔ WMS·LINK 형식이라 좌표 데이터 아닐 수 있음 (미사용) |

- 모든 거리는 **직선거리**. 지하철·버스·공원은 통합 데이터셋 열만 있고 화면(API) 노출 없음.
- `DATA_GO_KR_SERVICE_KEY` 는 Encoding 키(104자) → 코드가 Decoding(88자)으로 바꿔 씀. 오퍼레이션 경로(`get…`)는 포털 관례로 호출해 200 확인.

**실거래 비교 규칙** (`app/market.py`): 같은 유형(오피스텔↔오피스텔, 원룸↔단독/다가구·연립다세대), 월세 계약만, 최근 12개월, 면적 ±30%, 보증금 ±max(500만, 50%), 같은 동(10건 미만이면 같은 구). 결과: 표본 수, 월세 중앙값·25/75%, 백분위, 수준(10건 이상일 때만: 25% 미만 낮은 편 / 75% 초과 높은 편 / 그 사이 비슷), 최근 5건, 출처·조회 시점, 한계 안내(관리비 미포함, 단독/다가구는 계약면적 vs 매물 전용면적).
실측 (1,514건, 11-15): 비교 가능 1,508 · 같은 동 1,447 · 표본 중앙값 250 · 비슷 652 / 높은 편 580 / 낮은 편 272 / 판단 안 함 4. 1건 약 40ms.

**편의시설 규칙** (`app/amenities.py`): 300·500m 안 개수, 가장 가까운 곳(직선). 주점·노래방은 '상권 참고'로 따로 묶고 소음·안전으로 해석하지 않음. 실측 중앙값: 편의점 63m · 슈퍼 86m · 세탁소 114m · 약국 133m · 의원 113m · 빨래방 159m. 1건 약 10ms.

**건축물대장 표시**: 사용승인일, 지상·지하 층수, 승강기, 주차(건물 전체), 세대수, 주용도·세부 용도, 구조, 연면적. 빈 값은 비움(0 대치 안 함). 주용도가 근린생활시설이면 "주거용 계약·전입 가능 여부를 계약 전 확인" 안내만 (해당 388건, 판정 안 함).

### 5.5 정책 데이터

- 온통청년 청년정책 OPEN API: 회원 → 마이페이지 → 오픈 API 신청 → 담당자 승인. ⛔ **키 발급 대기**. 코드정의서로 대분류 `lclsfNm=주거`, 중분류 3종(주택 및 거주지 · 기숙사 · 전월세 및 주거급여 지원) 확인.
- `pipeline/collect_policies.py`: 주소·매개변수를 환경 변수로 받고, 옛/새 필드 이름 후보로 정규화(신청 방법 `plcyAplyMthdCn` 등), 못 맞춘 필드는 report. 원본 `DB/raw/youthcenter/`, 결과 `DB/staging/policies_*.json`. **첫 응답으로 필드 이름 확인 필요**.
- 그다음: LH 임대공고(data.go.kr 15058530, 활용신청 필요).
- 지금은 임시 목록 `DB/reference/policies_curated.json` (4.9).

### 5.6 거래 끝난 매물 처리

- jibsa `zigbang listing <id>` 가 거래 불가에 `LISTING_UNAVAILABLE`(직방 status=close·cancel) → 판정. `check_listing_status`: 고유 ID 하나씩 순서대로(병렬 금지, jibsa 요청 제한 준수), `--stale-hours 20` 안 확인분 건너뜀, 판정 active / unavailable / error(보류, 빼지 않음), 가격 변화는 기록만. 원본 `DB/raw/jibsa_status/<ts>.jsonl`, 결과 `DB/staging/listing_status_<ts>.csv`.
- `jibsa_batch_listing.py`: 한 프로세스에서 차례로 호출 (CLI 1회 시작 약 4초 → 건당 1~2초), 입력은 별도 스레드로 한 줄씩(파이프 막힘 해결).
- 11-16 결과 (고유 1,157개): 거래 가능 510 · 거래 불가 646 · 오류 1 (50905140, PARSE_ERROR, 유지).
- **11-16 사용자 결정: 새 버전을 만들지 않고 원래 후보 CSV 에서 행을 지운다** (`prune_unavailable`). 지운 행은 `DB/staging/pruned/<파일>_removed_<ts>.csv` + report (후보 CSV 는 Git 미추적이라 유일한 복구본).
- 재충전 시 겹침 제외: 이미 있는 ID / 주소·보증금·월세·면적 같고 층도 같거나 한쪽 미확인 / 거래 불가 ID. 경로 재사용은 같은 지번의 OK 경로를 **그 대학 건물만** 옮김 (처음 다른 대학 건물까지 옮겨져 백업에서 되돌린 뒤 고침).
- 매일 권장 순서: 상태 확인 → 새 매물 소량 수집 → 경로.
- 예전 매물끼리 주소·가격·면적 같은 묶음(층 미확인)은 건드리지 않음.

### 5.7 통합 매물 데이터셋 · Syntitan 준비

- `build_integrated` → `DB/staging/integrated_listings_<ts>.csv`. 최신 기록: 11-15 `…T191455` 1,514행 × 91열 (11-16 정리·재충전 뒤 "다시 생성"했다고 기록됨, 행 수 확인 필요). 1행 = 매물 1건: 유지비, 도보(건물 수·가장 가까운 건물), 같은 집 광고 수, 실거래 비교, 편의시설 8종 + 헬스장, 지하철·버스(300/500)·공원(500/1000) 거리·개수, `count_500m_bus_stop`, 건축물대장, floor·building_floor·room_type(광고 표기 그대로, `반지하`·`고층` 문자 포함), 각 '계산 못 한 이유' 열. 서비스와 같은 함수로 계산 → 화면 값과 같음. 추천 점수·순위는 조건마다 달라 넣지 않음.
- `export_syntitan` → `DB/export/syntitan/<ts>/` (원본 링크·수집 경로 열 제외, 외부 전송 없음): listings 1,514×29, rent_tx 85,191, stores 28,415, building_titles 620, walk_times 9,104, integrated_listings.
- CUBIG Syntitan: `.mcp.json` 에 `syntitan`(http, `https://mcp.syntitan.ai/mcp`) 등록. ⛔ **OAuth 인증 대기** (사용자가 `/mcp` 에서). MCP 에 업로드·내보내기 도구 없음 → 웹에서 직접 업로드. 승인 필요: ① 매물 CSV 외부 전송(jibsa 데이터 재사용 범위 확인) ② Start Refinement 크레딧. 계획: 진단만 먼저(스키마·컬럼 프로파일·AI Readiness) → 자체 기준선과 비교 → 정제는 비용 예상 보고 후.

### 5.8 카카오 쿼터

- 도보 경로 하루 **1,000회** (주소 검색 100,000회). 실패 여유로 하루 **960회 이하**. 주소 하나 = 건물 수만큼 호출, 같은 주소 매물은 한 번만.
- 11-15 은 2일차 952회 사용. 11-16 은 재충전 956회.
- 요청 때 계산(3.7: 700/일) · 자주 가는 곳(4.8: 200/일)도 같은 한도를 쓴다 (4.8 경고 참고).
- 카카오 장소 검색 결과는 저장 금지(메모리 캐시만). 도보 경로 응답의 장기 저장·공개 배포는 정책 확인 전 확정하지 않음.

---

## 6. FE

### 6.1 스택

React 19 · TypeScript · Vite 8 · Tailwind CSS v4 (`@theme` 토큰, `src/index.css`) · React Router 8 · lucide-react · Kakao Maps JS SDK(services, 마커 클러스터러) · oxlint · Noto Sans KR. FE 자동화 테스트 없음 (검증은 `tsc -b`, `vite build`, 헤드리스 Chrome 캡처).

### 6.2 화면

| 경로 | 페이지 | 내용 |
|---|---|---|
| `/` | `ExplorePage` | 필터(보증금 상한·면적·유형·유지비 상한 슬라이더, 기본값 = 프로필) · 지도(데이터 좌표 우선, 학교는 카카오 `SC4` 검색, 레벨 5+ 클러스터) · 목록(정렬: 유지비·월세·보증금·면적) · 비교 담기(최대 3) · "같은 집일 수 있음 · N건" 배지. 이 필터는 사용자가 고르는 값이라 여유 없음 |
| `/properties/:id` | `PropertyDetailPage` | 기본 정보, 강의동 도보시간(`WalkTimeCard`), 비용 카드(**예산 3단계**), 원본 링크(사이트 이름 표시), '거래 종료' 배지·'거래 가능 재확인' 행, `WhyRankCard`, `BuildingCard`, `MarketCompareCard`, `ContractCheckCard`, `AmenitiesCard` |
| `/profile` | `ProfilePage` | 5섹션 (6.3) |
| `/compare` | `ComparePage` | `RecommendPanel` + `DecisionAnalysis` + 비교 표 |
| `*` | `NotFoundPage` | |
| (전 화면) | `AppLayout` → `ChatPanel` | 오른쪽 아래 'AI 상담' 패널 (6.6) |

### 6.3 생활 프로필 (11-16 기준)

| # | 섹션 | 규칙 |
|---|---|---|
| 1 | 대학 | 4개 칩, 상단 대학 선택(`UniversityContext`)과 같은 값, 즉시 반영 |
| 2 | 수업 듣는 건물 (`DepartmentSection`) | **학과·학년 칸 없음 (11-16 사용자 결정)**. 학교 건물 전체(`GET /recommend/buildings`)에서 고름, 도보시간 일부·없음 건물은 '매물 n/N곳 · 고르면 계산' 안내 |
| 3 | 우선순위 | 예산·이동 시간·면적 중 누른 순서로 1·2순위, 55/30/15% 표시 |
| 4 | 조건 | 유지비 상한(30~150만 슬라이더), 보증금 상한(선택), 희망 통학(편도·도보·분), 희망 면적, 더 넓은 면적 |
| 5 | 시간표 | 요일·시작·종료·건물, `TimetableImageImport`(OCR 미리보기) |

- 저장 버튼을 눌러야 반영. 추천에 필요한 입력(`missingForRecommend`): 수업 듣는 건물, 1·2순위, 유지비 상한, 희망 통학, 희망 면적.
- '자주 가는 곳' 섹션은 뺐다(챗봇으로). 저장된 `places` 는 보존, 폼 저장 시 `exclusions`·`frequentPlaces` 유지.
- 예전 저장 형식(v3 카테고리, v2 `transit`)도 `parseStoredProfile` 이 읽어 남는 값만 옮긴다.

### 6.4 매물 비교 `/compare`

**추천 결과 (`RecommendPanel`)**: 상태(프로필 부족 / 서버 미설정 / 계산 중 — 5초 넘으면 '서버 깨우는 중' / 오류 / 완료). 완료 화면:
1. 조건 요약 + 통과 매물 수 + '장소 조건' 칩(제외 수, 해제)
2. 우선순위·실제 가중치, notes(호박색)
3. 정렬: AI 추천순 / 유지비 낮은 순
4. 목록: **처음 5개, '더 보기(+10개)', '접기'** (11-15 사용자 결정). 같은 집일 수 있는 광고는 지금 정렬에서 가장 앞선 하나만 보이고 "같은 집일 수 있는 광고 n건 더"로 펼침 (FE 표시만, 순위·점수는 엔진 그대로. '더 보기'는 묶은 '곳' 기준, 예: 이대 111개 → 99곳)
5. 각 행: 순위, 주소(상세 링크), '예산 초과'(여유 구간), 보증금·유지비·면적, 요소별 막대 + 기준 대비 값(좋으면 초록, 나쁘면 호박색), 종합 점수, 비교 토글. 상위 3개 강조
6. "순위를 바꾸면 위로 올라오는 매물"(`not_recommended`)
7. 제외된 매물(접힘): 단계별 개수 + 사유
8. 안내: 광고 매물, 도보시간은 카카오 도보 경로(건물 대표 지점), 예산 초과 규칙

**의사결정 분석 (`DecisionAnalysis`)**: 탭 4개, 탭을 고를 때 그 분석만 `POST /analysis/*` (추천과 같은 요청, 장소 조건 포함).
- 손해 없는 후보(Pareto): 월 유지비 × 통학 산점도 (frontier 보라 채움, 추천 상위인데 지배된 매물 빈 원, 상위 3 큰 점) + 표
- 돈 ↔ 통학(Trade-off), 순위 안정성(민감도: 조건 종류별 상위 3 유지율 막대 + 자주 상위권), 조건 완화(최소 완화와 새로 들어오는 매물)

**비교 표 (`CompareTable`)**: 2~3개. 최저 비용·최단 이동 카드, 비용(월 고정 주거비 최저 강조, **예산 3단계**), 강의동별 도보시간, 학과 건물 평균 도보시간. FE 가 매물 데이터로 직접 계산한 단순 비교 (추천 점수와 별개).

### 6.5 FE 계산 규칙

| 규칙 | 식 | 위치 |
|---|---|---|
| 유지비 | 월세 + 관리비 (하나라도 null → null) | `utils/cost.ts` |
| **예산 상태** (상세·비교 표) | `budgetStatus`: ≤ 상한 `within`(예산 이내) / ≤ 상한 + 10만 `slack`(예산 초과, 추천에 남음) / 그 위 `over`(추천 제외). 비용 null → 판단 안 함 | `utils/cost.ts` (`isWithinBudget` 삭제, 11-15) |
| 학과 건물 평균 도보 | 건물마다 1회, Σ ÷ 건물 수 (하나라도 없으면 null) | `utils/walk.ts`, `departmentVisits` |
| 같은 집일 수 있음 | 주소·보증금·월세·관리비·면적 모두 같음 → 배지만, 합치거나 지우지 않음 | `utils/duplicates.ts` |
| 기준 대비 문구 | 순위 설명 카드·추천 목록 공유 | `utils/vsTarget.ts` |
| 표기 | `1,000만원`, 지도 `87만`, `31.7㎡`, `14분`. 값 없으면 항상 "정보 없음" | `utils/format.ts` |

FE 의 점수 관련 숫자(`constants/recommend.ts`)는 화면 안내용 사본이다. **BE `scoring_config.py` 가 바뀌면 이 파일도 고친다** (여유 10만 원 `MONTHLY_BUDGET_SLACK_MANWON` 포함).

### 6.6 챗봇 패널 (`components/chat/ChatPanel.tsx`, `services/agentService.ts`, `types/agent.ts`)

- 레이아웃에 상주(화면 이동해도 대화 유지). 모드 '매물 추천' / '정책 안내' — 모드마다 session_id(sessionStorage)·대화 목록 따로. 새 대화 버튼은 session_id 새로 만듦.
- 첫 화면 예시 묶음 버튼(`EXAMPLE_GROUPS`): 생활 기능 5 · 정책·계약 5 (정책 찾기 / 비교 / 신청 절차 / 계약 안전 점검 / 지역 정책).
- 저장된 생활 프로필을 매 턴 보내고, **`profile_changed` 일 때만** 응답 조건을 생활 프로필·대학 선택에 저장. what-if·정책 모드는 저장 안 함.
- 카드: TOP 3(같은 집 광고 n건 더, 자주 가는 곳 도보), 비교(순위 ↑↓·"순위 밖"), 분석 카드(분석 7종 + `excluded`), 정책 카드(자격 판정 아님 배지·공식 링크·면책·비교·신청 절차·소득 표기·점검 목록), 확인 버튼, 상태 배지(정보 필요·AI 오류).

### 6.7 상태 저장 (localStorage, 읽기 실패 시 기본값)

| Context | 키 | 기본값 |
|---|---|---|
| `UniversityContext` | `home-poc:university` | 이화여자대학교 |
| `ProfileContext` | `home-poc:profile` | `EMPTY_PROFILE` |
| `CompareContext` | `home-poc:compare` | `[]` (최대 3) |
| 지오코딩 캐시 | `home-poc:geocode` | 좌표 없는 매물만 |
| 챗봇 모드 | `home-poc:agent-mode` (sessionStorage) | recommend |

로그인·서버 저장 없음.

### 6.8 디자인

- **보라색 중심** (11-15 사용자 결정): `src/index.css` 테마 값만 변경 — `navy #2b1a5e`, `blue #6d4ae6`, `blue-50 #f2eefe`. 클래스 이름은 그대로. green `#0f9d6b`/`#e7f7f0`, amber `#b45309`/`#fff7e6`, red `#d92d20`/`#fdecea`, text `#1a1f2c`, muted `#6b7280`, border `#e5e8ee`, bg `#f7f8fa`, radius 카드 16px / 컨트롤 12px.
- 브레이크포인트 sm 640 · md 768 · lg 1024. 탐색 데스크톱: 필터 288px · 지도 · 목록 400px. 상세 사이드 340px.
- Figma 파일 `LXbwlJ28cNfN13spFXFybO` (UI 초본 v2): `Tokens` 변수 16개 연결로 보라 반영 완료. ⛔ 02 매물 상세 참고 카드 3종, 05~07 상담 패널 분석 카드·예시 문구는 Figma Starter 플랜 MCP 호출 한도로 중단.

### 6.9 FE 알려진 문제·미구현

| 항목 | 상태 |
|---|---|
| 상세·비교 표 도보시간 | `spot1~4` 4개 건물만 씀 → 그 밖의 학과 건물은 "정보 없음" (추천은 BE 도보시간 표 사용) (확인 필요: 11-16 이후 변경 여부) |
| `ExplorePage` 빈 상태 문구 "현재는 이화여대 매물만…", `constants/universities.ts` 주석 | 로컬 4개 대학 데이터와 안 맞음 (확인 필요: 수정 여부) |
| `FE/README.md`, `BE/README.md` | 통합안 이전 설명이 남아 있을 수 있음 (확인 필요) |
| 매물 사진 | Placeholder |
| 지하철·버스·공원 거리 | API·화면 노출 없음 |
| A/B 선호, 시세 그래프 | 없음 |

---

## 7. 배포

| | 주소 | 설정 |
|---|---|---|
| FE | https://home-poc-frontend.vercel.app | Vercel, Root `FE`, `VITE_API_URL`(끝 `/` 없이), `VITE_KAKAO_MAP_KEY`. `VITE_*` 는 빌드 때 들어가므로 바꾸면 Redeploy. `vercel.json` 이 모든 경로를 `index.html` 로 |
| BE | https://home-poc.onrender.com | Render Blueprint(`render.yaml`: `home-poc-be`, Python 3.13.5, `rootDir: BE`, `uv sync --frozen --no-dev`, `uvicorn app.main:app`, 무료 플랜, 헬스 체크 `/health`). 대시보드 환경 변수 `CORS_ORIGINS`(Vercel 고정 주소), `KAKAO_REST_API_KEY`(비워도 됨) |
| 저장소 | 포크 `kimminkyung05/home-poc` 의 `feat/data-pipeline` (11-08 배포 설정) | 공용 `foxlover07/-2-` 는 팀 공유용, `main` PR 금지 |

순서: BE 먼저 → FE 에 BE 주소 → FE 주소를 BE `CORS_ORIGINS` 와 카카오 콘솔 Web 도메인에 등록 (안 하면 "카카오맵 SDK 스크립트를 불러오지 못했습니다").

알아둘 점
- **배포 데이터는 PoC 10건(`DB/poc_ewha.csv`, 이대)**. 수집 매물·도보 표는 `.gitignore` 대상이라 없다. 다른 대학은 "매물 데이터가 아직 없습니다". 공개 배포에 넣으려면 jibsa(비공식 수집) 이용 범위와 카카오 응답 저장 정책을 먼저 확인.
- Render 무료 플랜은 15분 무요청 시 잠듦 → 첫 요청 30초~1분, FE 는 5초 뒤 안내.
- 배포본은 아직 **통합안·챗봇·분석·참고 정보가 들어간 `feat/decision-support` 가 아님** (11-08 `feat/data-pipeline` 기준). 챗봇을 배포하려면 Render 에 LLM 키, 영속 대화 저장, OCR 미설치 처리(`ocr_unavailable`), 참고 데이터 파일 문제를 정해야 한다 (확인 필요).
- 배포본은 LLM·카카오를 호출하지 않는 구성이었다 (11-08). 지금 BE 는 `/recommend` 에서도 고른 건물의 빈 도보시간을 카카오로 계산할 수 있다(`BUILDING_WALK_KAKAO`) → 배포 시 키가 없으면 계산하지 않음 (확인 필요).

---

## 8. 결정 기록 (최신순)

| 날짜 | 결정 | 결정자 |
|---|---|---|
| 11-16 | LLM 순서 **Gemini 먼저 → EXAONE** (11-15 'EXAONE 우선' 뒤집음). 코드 기본값도 gemini → exaone | 사용자 |
| 11-16 | 챗봇 **'매물 추천' / '정책 안내' 모드 버튼**이 맞음 (11-15 '모드 버튼 없음' 대체) | 사용자 |
| 11-16 | 카카오 하루 1,000회를 수집·그때 계산·자주 가는 곳이 나눠 씀 — 인지. **건물 도보시간 전부 반영이 우선** | 사용자 |
| 11-16 | 시간표 사진은 **OCR 로만** (외부 LLM 전송 없음) | 사용자 |
| 11-16 | 거래 불가 매물은 새 버전 없이 **원래 후보 CSV 에서 행 삭제** (지운 행은 pruned 백업) | 사용자 |
| 11-16 | 생활 프로필에서 **학과·학년 칸 삭제**, 학교 건물 전체에서 수업 건물 고름 | 사용자 |
| 11-16 | 건물 도보시간 **B 방식**: 고른 건물의 빈 도보시간만 요청 때 계산 (요청 150·하루 700) | 사용자 |
| 11-16 | **자주 가는 곳은 챗봇 전용**: 카카오 검색 반경 3km, 상위 20개만 도보 계산, 하루 200회 | 사용자 |
| 11-16 | 정책 데이터는 온통청년 키 전까지 **임시 목록**(`policies_curated.json`) 사용, 데이터 없으면 고정 문장 | 진행 기록 |
| 11-15 | 장소 제외 조건은 **확인 질문 없이 바로 적용**, 답변 맨 앞에 '바뀐 것', 0개 남으면 되물음 | 팀 |
| 11-15 | 예산 **+10만 원 여유**를 FE 상세·비교 표에도 적용, 3단계(이내 / 여유 구간 / 추천 제외) | 사용자 |
| 11-15 | 미커밋 작업은 오류·정리 후 커밋. 최신 md 기준으로 문서 정리 | 사용자 |
| 11-15 | 주거 지원 정책은 **안내만** (자격 판정·지원금 점수 반영 제외), `CLAUDE.md` 0-7 수정 | 사용자 |
| 11-15 | 두 LLM 공급자 자동 전환(`ProviderChain`) 도입 (순서는 11-16 에 변경) | 사용자 |
| 11-15 | 추천 목록 처음 5개 + '더 보기' 10개씩. 매물 ID 가 나오는 문장은 그대로 (저작권) | 사용자 |
| 11-15 | 기준 엔진 = **통합안**, 새 개발은 worktree `home_poc-integrated` / `feat/decision-support`. 챗봇을 통합안으로 이전 | 사용자 |
| 11-15 | 디자인을 **보라색 중심**으로 | 사용자 |
| 11-13 | **추천 통합안** 확정: 3요소(예산·이동·면적), 공통 규칙 50점 ±5/−10, 교환비 10분=5만=2㎡, 0.55/0.30/0.15, 여유 +10만·×1.2, v3 삭제 | 팀 회의 |
| 11-10 | LangGraph 단일 에이전트, 의도·조건은 구조화 출력, 도구·매개변수는 서버가 결정·검증, 명세는 코드에서 생성 | 사용자 |
| 11-10 | **PBO 는 에이전트 로드맵에서 제외** (필요 시 독립 실험) | 사용자 |
| 11-10 | 확인 단계는 기존 입력이 지워지는 변경에만 (학교 변경·전체 되돌리기). 시간표 사진은 미리보기만, 확인 후 추가 | 사용자 |
| 11-10 | 대학별 추천(`university`), 대학 간 매물 ID 접두어 | 사용자 |
| 11-09 | 로컬 PoC 는 최대한 수집 (배포 안 함), 대학 확대: 고려대·서강대·서울과기대, 기본 강의동 4곳 임시 | 사용자 |
| 11-08 | 데이터 범위: 월세 원룸·오피스텔, 보증금 ≤ 3,000만 원. 이대 강의동 8곳 추가, 줄임말 표·중복 의심 표시 | 사용자 |
| 11-08 | 배포: Render + Vercel, 포크에서, 배포 데이터는 PoC 10건 | 사용자 |
| 11-07 | 추천 v2 기준 진행, **PBO 보류**, FE·BE 연결 우선 | 사용자 |
| 11-06 | 데이터 파이프라인: jibsa → 정규화 → 카카오 주소·도보 경로. 건물 좌표는 POI 대표 지점 | 사용자 |

대체된 결정 (기록만): 11-08 v3 카테고리 9개·예산 여유 0 (→ 11-13 통합안) · 11-10 Gemini 단독·시간표 Gemini 읽기 (→ 11-15 EXAONE 우선 → 11-16 Gemini 우선 + OCR) · 11-15 '정책 모드 버튼 없음' (→ 11-16 모드 버튼 확정).

---

## 9. 진행 상황 · 남은 일

### 9.1 기능별 상태 (2026-11-16)

| 기능 | 상태 | 근거·비고 |
|---|---|---|
| 추천 엔진 (통합안) | ✅ | `app/scoring.py`, 테스트 |
| 의사결정 분석 BE (Pareto·Trade-off·민감도·완화) | ✅ | `app/decision/`, `tests/test_decision.py` (13개), 엔진 결과·요청 불변 |
| 의사결정 분석 FE · 챗봇 연결 | ✅ | `DecisionAnalysis`, 의도 `analyze` |
| 순위 설명 카드 (XAI) | ✅ | `WhyRankCard` (LLM 없음) |
| 실거래·편의시설·건축물대장 BE·FE | ✅ | `/market/*`, `NearbyCards` |
| 계약 전 확인 카드 | ✅ (확인 필요: 진행 기록에 없음, 코드에 존재) | `/market/contract-check`, `ContractCheckCard` |
| 지하철·버스·공원 거리 | 🟡 | 통합 데이터셋 열만, API·화면 없음 |
| 챗봇 (통합안 엔진, 확인 단계, what-if, 되돌리기, 비교) | ✅ | `app/agent/`, ScriptedLLM 테스트 |
| 장소 제외 조건 | ✅ / ⚠️ | 실제 LLM 추출은 c31 등 일부만 확인. 헤드리스 Chrome 으로 FE 끝까지 1회 확인(11-15) |
| 자주 가는 곳 | 🟡 | 코드·테스트 있음(미커밋). 실제 카카오·LLM 동작 기록 없음 (확인 필요) |
| 건물 도보시간 그때 계산 (B 방식) | 🟡 | 코드·테스트 있음(미커밋). 실사용 기록 없음 (확인 필요) |
| 정책 안내 | 🟡 ⛔ | 임시 목록 6개로 동작. 온통청년 키 대기 |
| 시간표 OCR | 🟡 | 캡처 1장만 검증, Render 미설치 |
| 거래 불가 정리·재충전 | ✅ | 1,514 → 652 → 1,456 |
| 선호 프로필 (명시·추론 구분, 이력) | 🟡 | 명시 입력만, 되돌리기 1단계 |
| 피드백 수집 (A/B·좋아요) · 개인화 가중치 · PBO | ⬜ 🧪 | 고정 가중치만. PBO 는 독립 실험으로만 |
| 능동적 선호 질문 | 🟡 | 필수 조건 질문만. 민감도 측정상 "1·2순위 확인" 질문이 순위를 가장 크게 바꿈 → 후보 |
| 미완성 기준 (심야 귀가·오르막·조용함 점수·생활 리듬) | ⬜ | |
| 유사 사용자 추천 | ⬜ ⛔ | 실제 사용자 데이터 없음 |
| CUBIG Syntitan | ⛔ | OAuth 대기, 업로드 파일·기준선 준비됨 |
| 대화 영속 저장 · 답변 스트리밍 | ⬜ | |
| Prompt injection 전용 처리·테스트 | ⬜ | |
| 정량 평가 (Experiment A~E) | 🟡 | A: EXAONE/Gemini 40개 1회. NVIDIA 등 다른 모델·B~E 없음 |

### 9.2 측정 결과 요약

**의사결정 분석** (11-15, 로컬 1,207건 = 정리 전 데이터, 4개 대학 × 3개 조건 = 12케이스; 조건: 데이터 가장 많은 건물 1곳, 통학 15분, 면적 20㎡, 보증금 1,000만, (이동/예산, 60)·(예산/면적, 60)·(이동/예산, 45))

| 지표 | 값 |
|---|---|
| 필터 통과 후보 | 38 ~ 249 |
| Pareto frontier 비율 | 평균 0.285 |
| 엔진 상위 3이 frontier 위 | 35/36 |
| 조건 종류별 TOP 3 평균 유지율 (최솟값) | 우선순위 **0.428** (0.267) · 가중치 0.778 (0.6) · 희망 면적 0.861 (0.5) · 넓은 면적 선호 0.861 (0.667) · 유지비 ±5/10 0.924 (0.583) · 희망 통학 ±5 0.958 (0.667) |
| 최소 Kendall tau | 우선순위 0.19 · 가중치 0.491 · 유지비 0.667 · 면적 0.867 · 넓은 면적 0.894 · 통학 0.92 |
| 조건 완화 (목표 10개) | 12케이스 모두 유지비 +5만 한 단계. 보증금은 +500~+2,000만 |
| 실행 시간 | 엔진 0.006~0.026초, 민감도(21회) 최대 1.03초, 완화(10회) 최대 0.93초 |

해석: 순위는 **우선순위 순서에 가장 민감**. 12개 가정 조건 측정이며 실제 사용자 선호로 검증된 것이 아니다.

분석 상세: 민감도 시나리오 20~21개(우선순위 5 · 가중치 5 · 유지비 ±5/±10 · 희망 통학 ±5 · 희망 면적 ±2 · 넓은 면적 선호 2 · 시간표 제거). 완화 lever: 유지비 +5~30, 보증금 +500~2000/해제, 시간표 건물 제외, 챗봇 조건 제외 (잠근 조건은 안 건드림, 결과는 전부 `hypothetical`). 희망 통학·면적은 필터가 아니라 완화 lever 에 없음. Pareto 의 공강 귀가·생활 동선 목적은 엔진에 계산이 없어 `unsupported`.

### 9.3 테스트 기록

| 날짜 | 범위 | 결과 |
|---|---|---|
| 11-15 | 원래 트리 BE | 204 passed → EXAONE 추가 후 214 |
| 11-15 | worktree BE (단계별) | 147 → 158/159 → 231 → 236 → 238 → 243 → 246 → 251 → 257 → 280 → 285 → 287 → 291 passed (+1 skipped: OCR 캡처 파일 없음) |
| 11-16 | worktree BE (정리·재충전·LLM 순서 변경 후) | **307 passed / 1 skipped** |
| 11-16 | 순서 변경 후속 (분/하루 429, 시간 상한, 시간표 LLM 미호출) | LLM·에이전트·정책 테스트 90 통과. 전체 실행 시 건물 관련 5개 실패 — 같은 시각 `buildings.py`·`building_walks.py` 작업 때문으로 기록 (**미해결, 다시 돌려 확인 필요**) |
| 11-15~16 | FE | `tsc -b` 통과, `vite build` 통과 |

실행: `cd home_poc-integrated/BE && ../../home_poc/home/Scripts/python.exe -X utf8 -m pytest -q`, `cd FE && npm run build`. 실제 LLM 호출 테스트·평가는 승인 후.

### 9.4 미커밋 상태 (11-16)

`feat/decision-support` 의 마지막 커밋 `ac762c7`(11-15 푸시) 이후 미커밋: 에이전트·LLM·정책·scoring·data·models·routers(agent·market·recommend), FE 챗봇·비교 표·상세·프로필·서비스·타입·utils 수정 + 새 파일 `building_walks.py`, `frequent_places.py`, `listing_status.py`, `pipeline/{check_listing_status, jibsa_batch_listing, prune_unavailable, refill_listings}.py`, 테스트 4개, `DB/reference/`. 원래 트리(`home_poc`)에도 옛 챗봇 미커밋 작업이 남아 있다. → 9.3 의 실패 5개를 정리한 뒤 커밋.

### 9.5 다음 할 일 (승인 필요 항목 포함)

1. 전체 테스트 다시 실행 → 건물 관련 실패 정리 → 묶어서 커밋 (11-15 결정)
2. 카카오 일일 한도 배분 정하기: 경로 배치 960 vs 요청 때 계산 700 + 자주 가는 곳 200 (4.8)
3. 새 매물의 추가 강의동 경로 (배치로 할지 B 방식에 맡길지)
4. 챗봇 제외 조건 데이터: 서비스 CSV 를 통합 데이터셋으로 바꿀지 결정 (3.8)
5. 온통청년 키 신청(사용자) → 첫 수집으로 필드 확인 → LH 임대공고
6. 평가 세트 52개 실제 LLM 재실행 (Gemini 우선 기준, c41~c52 포함) — 호출 승인 후
7. 자주 가는 곳·B 방식 실제 동작 확인 (카카오 소량 호출, 승인 후)
8. Syntitan OAuth(사용자) → 진단만 먼저 → 정제는 비용 보고 후
9. 매일: 거래 상태 확인 → 새 매물 소량 → 경로
10. 배포 갱신 여부 결정 (7장)
11. Figma 남은 화면 (플랜 한도)
12. 후속: 영속 대화 저장, 답변 스트리밍, 지하철·버스·공원 화면 노출, Prompt injection 테스트

### 9.6 팀 확인이 필요한 임시값

대학별 기본 강의동 4곳, 이대 학과 → 건물 예시표(3개 학과), 건물 줄임말 표(`ALIASES`), 장소 조건 기본 반경(피하기 100m·가까이 300m), 교환비·가중치·여유 금액(전부 초기값).
