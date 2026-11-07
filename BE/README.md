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
- `POST http://localhost:8000/recommend` (아래 "추천 점수 v2")
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
│   ├── data.py              # CSV 로딩 (빈 값 → null, 숫자 열 → 숫자)
│   ├── config.py            # 데이터 파이프라인·외부 API 설정
│   ├── scoring_config.py    # 추천 점수 v2 설정값 (설계안 8장) + 학과 → 건물 예시표
│   ├── scoring.py           # 추천 점수 v2 (필터, 요소별 점수, 종합 점수, 비추천 사유)
│   ├── tools/               # Kakao API (데이터 파이프라인용)
│   └── routers/
│       ├── properties.py    # GET /properties, GET /properties/{property_id}
│       └── recommend.py     # POST /recommend
├── pipeline/                # 매물 수집·정규화, 도보 경로 (오프라인 스크립트)
├── scripts/ask.py           # LLM 호출 실험
└── tests/
    ├── test_properties.py
    ├── test_scoring_time.py     # 1단계 시간 점수 함수
    ├── test_scoring_cost.py     # 2단계 예산 필터 · 주거비
    ├── test_scoring_travel.py   # 3단계 이동시간
    ├── test_scoring_combine.py  # 4단계 종합 점수 · 비추천 사유
    ├── test_recommend_api.py    # 5단계 POST /recommend
    ├── test_jibsa_adapter.py
    └── test_kakao.py
```

## 추천 점수 v2 (`app/scoring.py`)

팀 설계안 「추천 점수 함수 설계안 v2」(2026-11-07)를 구현한다. 지금은 설계안 10장 구현 순서 중 **1~5단계**까지 했다.
v1(`criteria.py`의 기준 6개·반영 비율 %, `recommend.py`의 중요도 1~5·min-max)은 v2로 대체해 지웠다.

- 요소는 **이동시간 · 교통 편의성 · 주거비** 3개다. 각 요소는 매물별 **절대 점수(0~100)** 라서 다른 매물에 따라 바뀌지 않는다.
- 사용자는 **1순위·2순위만** 고른다. 가중치는 1·2·3순위 순으로 **0.55 / 0.30 / 0.15**.
- 계산은 전부 코드가 하고 LLM은 쓰지 않는다. 숫자는 전부 `app/scoring_config.py`에서 읽는다.

### 계산

| 단계 | 계산 | 설계안 |
| --- | --- | --- |
| 필터 | 월세+관리비 > 예산 + 10만 원이면 제외. 보증금 > 보증금 예산 × 1.2이면 제외 (보증금 예산을 입력한 경우만). 예산 ~ 예산 + 10 사이는 `over_budget: true`, 주거비 0점 | 2장 |
| 시간 점수 | `t ≤ T`: `100 − 15·t/T`, `t > T`: `85 · 2^(−(t−T)/h)`. 0분 100점, T분 85점, 그 뒤로 h분마다 절반 | 3장 |
| 이동시간 | 학과 건물 0.4 · 자주 가는 곳 0.4 · 시간표 0.2. 부분별 **편도 평균**을 시간 점수로 바꿔 합친다. 학과 건물과 시간표는 T = 희망 통학 시간, h = 10, 도보 | 4.1장 |
| 주거비 | `100 × (예산 − 월 고정 주거비) ÷ (예산 × 0.8)`를 0~100으로 자름. 예산의 20% 이하면 100점, 예산과 같으면 0점. **임시 완화:** 설계안 초기값은 분모 0.4(60% 이하 100점)인데, 너무 가팔라 이동시간을 1순위로 골라도 주거비가 더 크게 작용해서 바꿨다. 테스트 케이스로 값을 정할 예정 | 4.3장 |
| 종합 | `0.55 × 1순위 + 0.30 × 2순위 + 0.15 × 3순위`. 동점이면 매물 ID 순, 최대 50개 | 5장 |
| 비추천 사유 | 다른 5가지 1·2순위 조합 중 어디서든 상위 3위 안인데 지금 화면(현재 조합 상위 `displayed_top_n`위) 밖인 매물, 최대 3개 | 6장 |

- **계산할 수 없는 부분·요소:** 입력이나 데이터가 없는 부분의 비중은 나머지에 비율대로 나누고, `notes`와 `assumptions`에 남긴다.
  - 지금은 **자주 가는 곳**(이동시간 안)과 **교통 편의성**(요소)이 항상 여기에 해당한다. 시간표를 입력하지 않았을 때도 같다.
  - 교통 편의성을 1·2순위로 골랐으면 "선택한 순위가 반영되지 않았습니다"라고 안내한다.
- **비추천 사유:** 지금 1위 매물과 비교해 **가중 점수 차이가 가장 큰 요소**를 사유로 쓴다.
  - 점수가 없는(null) 요소는 후보에서 뺀다.
  - 이동시간이 사유면, 세부 부분(학과 건물/시간표) 중 점수가 가장 낮은 부분의 값으로 문장을 만든다.
  - 후보가 3개를 넘으면 다른 조합에서의 최고 순위가 높은 순, 같으면 ID 순으로 고른다.
  - `message`는 코드가 만든 기본 문장이다. LLM은 이 문장을 다듬기만 한다(10장 8단계).
- **학과 → 건물:** `DEPARTMENT_BUILDINGS`는 **예시값이라 팀 확인이 필요하다.** 표에 없는 학과는 `department_buildings`로 직접 입력한다.
- **이동시간 데이터:** `DB/poc_ewha.csv`의 강의동 4개(ECC, 이화·포스코관, 학관, 공과대학) 도보시간만 쓴다. 이 밖의 건물 이름은 422 오류.
- **학년:** 받기만 하고, 학과 건물 비중은 0.4로 고정한다.
- **아직 없는 것:** 교통 편의성(10장 6단계), 자주 가는 곳 + 대중교통 추정(7단계), 특장점 · 시간표 사진 인식 · 줄임말 매핑(8단계).

### `POST /recommend`

입력:

```json
{
  "budget_manwon": 75,
  "deposit_budget_manwon": 1000,
  "department": "컴퓨터공학전공",
  "department_buildings": null,
  "grade": 2,
  "desired_commute_min": 15,
  "timetable": [{"day": "월", "start": "09:00", "end": "10:15", "building": "ECC"}],
  "priorities": ["travel", "cost"]
}
```

| 필드 | 필수 | 설명 |
| --- | --- | --- |
| `budget_manwon` | ✅ | 월 고정 주거비 예산 (월세 + 관리비, 만원, 0보다 큼) |
| `deposit_budget_manwon` | | 보증금 예산 (만원). 없으면 보증금 필터 생략 |
| `department` | ✅ | 학과. 예시표에 있으면 학과 건물을 자동으로 채운다 |
| `department_buildings` | | 학과 건물 직접 입력 (주면 예시표 대신 사용) |
| `grade` | ✅ | 학년 (1 이상) |
| `desired_commute_min` | ✅ | 희망 통학 시간 (편도, 도보, 분) |
| `timetable` | | `[{day, start, end, building}]`. 시각은 `HH:MM` |
| `priorities` | ✅ | 1·2순위. `travel`(이동시간) / `transit`(교통 편의성) / `cost`(주거비) 중 서로 다른 2개 |

출력:

| 필드 | 내용 |
| --- | --- |
| `priorities` | 1·2·3순위 |
| `weights` | 실제로 쓴 가중치 (계산 못 한 요소는 0, 합 1) |
| `ranked[]` | `rank`, `property_id`, `score`, `factor_scores`(요소별 0~100, 없으면 null), `contributions`, `travel_parts`(학과 건물·시간표의 `avg_min`·`target_min`·`score`·`weight`), `monthly_cost_manwon`, `deposit_manwon`, `over_budget` |
| `not_recommended[]` | `property_id`, `best_rank_elsewhere`, `top_in`(상위 3위 안인 다른 조합), `weak`·`strong`(요소, 값, 1위 값, 점수, 가중 점수 차이), `message` |
| `excluded[]` | `property_id`, `reason` (예: "예산보다 월 12만 원 비싸서 제외") |
| `assumptions` | 사용한 T, h, 가중치(순위 기준·실제), 여유 금액, 학과 건물과 그 출처, 학년, 설정값 전체 |
| `notes` | 계산에서 뺀 부분·요소 등 안내 |

요소별 점수는 순위 조합과 상관없이 같다. 그래서 결과 화면에서 1·2순위를 바꾸면 `factor_scores`와 가중치만으로 바로 다시 정렬할 수 있다.

### 팀 의견 요청

1. **학과 건물 비중 40%의 근거:** 학과 건물을 이동시간에서 40%로 크게 반영한 이유는, 학기마다 시간표가 바뀌어 학과 건물이 장기적으로 더 중요하다고 봤기 때문이다. 실제로 학과 건물 수업이 많은지는 확인이 필요하다 (특히 1학년은 전공 수업이 적다). 수강편람으로 확인하는 안, 학년별로 비중을 바꾸는 안에 대한 의견.
2. **학과 건물 두 번 반영:** 시간표에 학과 건물 수업이 있으면 학과 건물이 두 번 반영된다. 의도와 같은 방향이라 그대로 두었는데 괜찮은지.

### 알려진 한계

1. 대중교통 시간은 지하철 기준 추정값이다. 버스가 더 빠른 경우를 놓치며, 추정값은 실제보다 길게만 나온다 (비용 때문에 ODsay를 쓰지 않음).
2. 관리비 정보가 없는 매물의 처리 방식은 미정이다. 지금은 "월세 또는 관리비 정보 없음"으로 제외한다.
