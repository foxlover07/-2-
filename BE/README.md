# BE — AI 주거 추천 서비스 백엔드

FastAPI 기반 백엔드 뼈대. 데이터는 `../DB/poc_ewha.csv`(UTF-8 BOM)를 읽어 메모리에 올린다.

## 준비

[uv](https://docs.astral.sh/uv/)가 필요하다. `BE/` 폴더에서:

```bash
uv sync
```

환경 변수가 필요하면 `.env.example`을 복사해 `.env`로 만든다 (비워 두면 기본값 사용).

```bash
cp .env.example .env
```

OpenRouter API 키 받는 법

1. 오픈라우터 가입
2. 대쉬보드에서 API keys 클릭
3. 새 키 만들어서 생성된 키 바로 복사(키는 처음에 만들 때만 전부 보여준다고 합니다)
4. 저는 모델에 google/gemma-4-26b-a4b-it:free 썼습니다

| 키 | 기본값 | 설명 |
| --- | --- | --- |
| `CORS_ORIGINS` | `http://localhost:5173` | 쉼표로 구분한 허용 Origin |
| `DATA_CSV_PATH` | `../DB/poc_ewha.csv` | 매물 CSV 경로 |
| `OPENAI_API_KEY` | (필수, `scripts/ask.py`) | OpenRouter API 키 |
| `OPENAI_BASE_URL` |`https://openrouter.ai/api/v1`|
| `LLM_MODEL` | (필수, `scripts/ask.py`) | 모델 이름 (OpenRouter는 `제공자/모델` 형식) |

## 실행

```bash
uv run uvicorn app.main:app --reload
```

- http://localhost:8000/health
- http://localhost:8000/properties
- http://localhost:8000/properties/P001
- `POST http://localhost:8000/recommend` (아래 "추천")
- API 문서: http://localhost:8000/docs

## LLM 호출 실험 (`scripts/ask.py`)

`.env`에 `OPENAI_API_KEY`, `LLM_MODEL`(OpenRouter면 `OPENAI_BASE_URL`도)을 넣은 뒤:

```bash
uv run python scripts/ask.py
```

일반 호출과 JSON 호출을 차례로 실행한다. 하나만 돌리거나 문장을 바꾸려면:

```bash
uv run python scripts/ask.py chat "이대 근처 원룸 시세가 어때?"
```

```bash
uv run python scripts/ask.py extract "학관은 매일 가고 예산은 70만원"
```

- **chat**: 시스템 프롬프트 "너는 대학생 자취방 추천 도우미야. 모르면 모른다고 답해." + 질문 → 답변, 토큰 사용량
- **extract**: 문장 → `{"visits": {강의동: 횟수}, "budget_manwon": 숫자|null, "cost_importance": 1~5, "walk_importance": 1~5}`
  - 강의동 이름은 CSV의 `spot1~4_name`에서만 고르게 하고, 목록에 없는 이름은 버린다
  - JSON 파싱에 실패하면 기본값 `{"visits": {}, "budget_manwon": null, "cost_importance": 3, "walk_importance": 3}`

## 테스트

```bash
uv run pytest
```

## 구조

```
BE/
├── app/
│   ├── main.py              # FastAPI 앱, CORS, /health
│   ├── models.py            # Pydantic Property 모델 (FE/src/types/property.ts 와 1:1)
│   ├── data.py              # CSV 로딩 (빈 값 → null, 숫자 열 → 숫자), 도보시간 표
│   ├── config.py            # 데이터 파이프라인·외부 API 설정
│   ├── buildings.py         # 시간표 건물 이름 정규화·줄임말
│   ├── scoring_config.py    # 추천 설정값 (통합안 6장) + 학과 → 건물 예시표
│   ├── scoring.py           # 추천 (필터, 요소별 점수, 종합 점수, 비추천 사유)
│   ├── exclusions.py        # 챗봇 제외 조건: 확인 계획 형식 + 확인 도구 실행 (챗봇은 미완성)
│   ├── tools/               # Kakao API (장소 검색·도보 경로·주소)
│   └── routers/
│       ├── properties.py    # GET /properties, GET /properties/{property_id}
│       └── recommend.py     # POST /recommend, GET /recommend/buildings
├── pipeline/                # 매물 수집·정규화, 도보 경로 (오프라인 스크립트)
├── scripts/ask.py           # LLM 호출 실험
└── tests/
    ├── test_properties.py
    ├── test_scoring_rule.py     # 공통 규칙 · 교환비 · 3.4장 예시표 · 설정값
    ├── test_scoring_filters.py  # 필터 순서·사유 · 대학별
    ├── test_scoring_travel.py   # 이동 시간 (학과 건물 · 시간표) · 건물 이름
    ├── test_scoring_combine.py  # 종합 점수 · 비추천 사유 · assumptions
    ├── test_exclusions.py       # 챗봇 제외 조건 도구·실행
    ├── test_recommend_api.py    # POST /recommend
    ├── test_jibsa_adapter.py
    └── test_kakao.py
```

## 추천 (`app/scoring.py`)

팀의 「추천 시스템 통합안」(2026-11-13 회의 확정안, `docs/recommend-integrated-design.md`)을 구현한다.
지금은 통합안 8장 구현 순서 중 **1~4번**까지 했다. 5번 챗봇은 미완성이다.
v3 카테고리 추천(`categories.py`, `GET /recommend/categories`)과 `POST /recommend/v2`는 이 통합안으로 합쳐 지웠다.

- 요소는 **예산 · 이동 시간 · 면적** 3개다. 매물마다 **절대 점수(0~100)** 라서 다른 매물에 따라 바뀌지 않는다.
- 사용자는 **1순위·2순위만** 고른다. 가중치는 1·2·3순위 순으로 **0.55 / 0.30 / 0.15**.
- 계산은 전부 코드가 한다. 숫자는 전부 `app/scoring_config.py`에서 읽는다.

### 필터 (순서대로, 걸리면 사유를 남기고 제외)

| 순서 | 조건 | 제외 사유 예 | `stage` |
| --- | --- | --- | --- |
| 1 | 요청한 대학의 매물만 | (목록에 넣지 않음) | |
| 2 | 유지비(월세 + 관리비) > 유지비 상한 + 10만 원 | "유지비가 상한보다 월 12만 원 많아서 제외" | `budget` |
| 3 | 보증금 > 보증금 상한 × 1.2 (입력한 경우만) | "보증금이 상한보다 300만 원 많아서 제외" | `deposit` |
| 4 | 챗봇 제외 조건 (`exclusions`) | "주변 150m 안 술집 5곳" | `exclusion` |
| 5 | 월세·관리비, 면적 또는 도보시간 정보 없음 | "도보시간 정보 없음: 공과대학" | `missing` |

유지비가 상한과 상한 + 10만 원 사이인 매물은 남기고 `over_budget: true`("예산 초과")로 표시한다.

### 점수: 공통 규칙과 교환비

```
점수 = 50 + 5 × (기준보다 좋은 쪽 단위 수)    기준보다 좋을 때
     = 50 − 10 × (기준보다 나쁜 쪽 단위 수)   기준보다 나쁠 때   → 0~100으로 자름
단위(교환비): 편도 10분 = 월 5만 원 = 2㎡
```

| 요소 | 기준 | 좋은 쪽 (+5점/단위) | 나쁜 쪽 (−10점/단위) |
| --- | --- | --- | --- |
| 이동 시간 | 희망 통학 시간 (편도) | 희망보다 가까움 (10분) | 희망보다 멂 (10분) |
| 예산 | 유지비 상한 | 상한보다 쌈 (5만 원) | 상한 초과, 여유 구간 (5만 원) |
| 면적 | 희망 면적 | 넓은 면적 좋음 +5 · 상관없음 0 · 싫음 −2.5 (2㎡) | 희망보다 좁음 (2㎡) |

- **이동 시간**은 학과 건물 0.4 · 자주 가는 곳 0.4 · 시간표 0.2. 부분마다 편도 평균에 규칙을 적용한 다음 합친다. 자주 가는 곳은 데이터 준비 중이라 늘 빠지고, 시간표가 없을 때도 그 비중을 나머지에 나눈다.
- 예 (희망 15분, 상한 75만 원, 희망 20㎡): 이동 0분 57.5 · 25분 40 / 유지비 45만 80 · 85만 30 / 면적 16㎡ 30 · 30㎡ 좋음 75, 싫음 37.5.
- 교환비는 매물 1,175개 회귀분석의 시장 가격으로 정했다 (통합안 3.2장). 학생이 느끼는 통학의 무게보다 작을 수 있다.

### 종합과 결과

- 종합 점수 = `0.55 × 1순위 + 0.30 × 2순위 + 0.15 × 3순위`. 점수 높은 순 → 유지비 낮은 순 → 매물 ID 순.
- **비추천 사유:** 다른 5가지 1·2순위 조합 중 어디서든 상위 3위 안인데 지금 상위 3위(`displayed_top_n`) 밖인 매물, 최대 3개. 지금 1위와 비교해 가중 점수 차이가 가장 큰 요소를 사유로 쓴다. 이동 시간이 사유면 세부 부분 중 점수가 가장 낮은 부분의 값을 쓴다.
- **기준 대비 값:** `vs_target`(희망보다 몇 분 / 예산보다 몇 만 원 / 희망보다 몇 ㎡). 기준을 맞추면 50점이라 점수 자체는 낮아 보일 수 있어, 화면에는 이 값을 보여 준다 (통합안 3.6장).
- **학과 → 건물:** `DEPARTMENT_BUILDINGS`는 이대 **예시값 3개뿐이라 팀 확인이 필요하다.** 다른 대학과 표에 없는 학과는 `department_buildings`로 직접 입력한다.
- **학년:** 받기만 하고 계산에는 쓰지 않는다.

### 챗봇 제외 조건 (`app/exclusions.py`) — 챗봇은 미완성

사용자가 승인한 **확인 계획**을 `exclusions`로 넣으면 코드가 매물마다 확인한다. 말 → 계획을 만드는 챗봇(LLM)은 아직 없어서, 지금은 계획 JSON을 직접 넣어 테스트한다.

| 도구 | 하는 일 | 필요한 칸 |
| --- | --- | --- |
| `nearby_count` | 반경 N m 안 장소 개수 (키워드 여러 개는 장소 ID로 중복 제거) | `keywords` 또는 `category`, `radius_m` |
| `nearest_distance` | 가장 가까운 장소까지 거리. 반경(기본 1000m) 안에 없으면 '없음' | `keywords` 또는 `category` |
| `listing_field` | 매물 칸 조건 (면적, 보증금, 월세, 관리비) | `field` |

```json
{
  "source_text": "밤에 시끄러우면 잠을 못 자요",
  "conditions": [{"tool": "nearby_count", "label": "술집", "keywords": ["술집", "호프", "클럽"],
                  "radius_m": 150, "exclude_if": {"op": ">", "value": 2}}],
  "unsupported": [],
  "confirm_message": "주변 150m 안에 술집이 3곳 이상인 매물을 뺄게요. 괜찮으세요?"
}
```

- 장소 도구는 카카오 장소 검색을 쓴다 (`KAKAO_REST_API_KEY`). 키가 없으면 장소 조건은 확인하지 않고 notes로 안내한다.
- 매물 좌표가 없으면 빼지 않고 notes에 개수를 남긴다. **PoC 매물 10개는 좌표가 비어 있어** 장소 도구를 시연하려면 좌표가 있는 수집 데이터가 필요하다.
- 추천 한 번에 카카오 호출 상한 500(설정). 넘으면 남은 매물은 확인하지 않고 "조건을 줄여 주세요"라고 안내한다.
- 같은 매물·도구·키워드·반경의 결과는 **프로세스 메모리에만** 캐시한다 (카카오 결과 저장 정책은 확인 필요).
- `unsupported`(도구로 확인할 수 없는 기호)는 추측해서 빼지 않고 "아직 확인할 수 없어요"라고 안내한다.
- 결과의 `exclusion_counts`에 조건별로 빠진 매물 수가 있다.

### `POST /recommend`

입력 (통합안 1.2장):

```json
{
  "university": "이화여자대학교",
  "department": "컴퓨터공학전공",
  "department_buildings": null,
  "grade": 2,
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

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `university` | | 이 대학 매물만 추천 (기본 이화여자대학교) |
| `department` | ✅ | 학과. 이대 예시표에 있으면 학과 건물을 자동으로 채운다 |
| `department_buildings` | | 학과 건물 직접 입력 (다른 대학은 필수) |
| `grade` | ✅ | 학년 (1 이상, 계산에는 안 씀) |
| `priorities` | ✅ | 1·2순위. `travel`(이동 시간) / `cost`(예산) / `area`(면적) 중 서로 다른 2개 |
| `budget_manwon` | ✅ | 유지비(월세 + 관리비) 상한, 만원 |
| `deposit_budget_manwon` | | 보증금 상한. 없으면 보증금 필터 생략 |
| `desired_commute_min` | ✅ | 희망 통학 시간 (편도, 도보, 분) |
| `desired_area_m2` | ✅ | 희망 면적 (㎡) |
| `larger_area` | | 더 넓은 면적: `like` / `neutral`(기본) / `dislike` |
| `timetable` | | `[{day, start, end, building}]`. 건물 줄임말은 표준 이름으로 읽는다 |
| `exclusions` | | 승인된 챗봇 확인 계획 목록 |

출력:

| 필드 | 내용 |
| --- | --- |
| `priorities`, `weights` | 1·2·3순위와 실제로 쓴 가중치 |
| `ranked[]` | `rank`, `score`, `factor_scores`(travel/cost/area), `contributions`, `travel_parts`, `vs_target`, `monthly_cost_manwon`, `deposit_manwon`, `area_m2`, `over_budget` |
| `not_recommended[]` | `best_rank_elsewhere`, `top_in`, `weak`·`strong`, `message` |
| `excluded[]` | `property_id`, `reason`, `stage`, `condition`(챗봇 조건 이름) |
| `exclusion_counts` | 챗봇 조건 이름 → 빠진 매물 수 |
| `assumptions` | 대학, 여유 금액, 희망 시간·면적과 넓은 면적 선택, 가중치(나눈 결과 포함), 승인된 챗봇 조건, 설정값 전체 |
| `notes` | 계산하지 못한 부분 등 안내 |

### 남은 것과 한계 (통합안 9장)

- **학과 → 건물 표:** 이대 예시 3개뿐이다. 다른 대학은 사용자가 건물을 고른다.
- **매물 좌표:** 레포의 PoC 매물 10개는 좌표가 비어 있다.
- **챗봇:** 미완성. 확인 도구의 기본값(소음 → 키워드·반경·개수 등), LLM 모델, 프롬프트, 확인 화면을 정해야 한다.
- **교환비:** 시장 가격으로 정한 값이라 학생이 느끼는 통학의 무게보다 작을 수 있다. "A가 B보다 위여야 한다" 형식의 테스트 케이스로 확인한다.
- **도로·소음:** 카카오 장소 검색으로는 확인할 수 없다. 추후 도구로 추가한다.
- **대중교통 시간:** 지금은 도보만 쓴다.
- **관리비 정보가 없는 매물:** 지금은 "월세 또는 관리비 정보 없음"으로 제외한다.
