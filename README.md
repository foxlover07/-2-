# BE — AI 주거 추천 서비스 백엔드

FastAPI 기반 백엔드 뼈대. 데이터는 `../DB/poc_ewha.csv`(UTF-8 BOM)를 읽어 메모리에 올린다.

## 준비

[uv](https://docs.astral.sh/uv/)가 필요하다. `BE/` 폴더에서:

```bash
uv sync
```

환경 변수가 필요하면 `.env.example`을 복사해 `.env`로 만든다 (비워 두면 기본값 사용).

```bashhttps://github.com/foxlover07/-2-/blob/feat/be-skeleton/BE/README.md
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
│   ├── recommend.py         # 추천 점수 (주거비·도보시간 가중합)
│   ├── criteria.py          # 학생 맞춤 추천 근거 + 다기준 점수 (docs/student-criteria.md)
│   └── routers/
│       └── properties.py    # GET /properties, GET /properties/{property_id}
└── tests/
    ├── test_properties.py
    ├── test_recommend.py
    └── test_criteria.py
```

## 학생 맞춤 추천 근거 (`app/criteria.py`)

노션 "학생 맞춤 추천 근거" 표의 MVP 근거를 계산한다. 근거 표는 `docs/student-criteria.md`.

| 기준 키 | 근거 | 방향 | 입력 |
| --- | --- | --- | --- |
| `monthly_cost` | 월 고정 주거비 | 낮을수록 | CSV |
| `weekly_commute` | 주간 총 통학 시간 | 낮을수록 | 시간표 (Vision LLM 인식 예정) |
| `gap_home` | 공강 귀가 가능 횟수 | 높을수록 | 시간표 |
| `frequent_place` | 자주 가는 곳 이동시간 (알바 포함) | 낮을수록 | 선택한 지역 + 이동시간 |
| `late_return` | 심야 귀가 시간 | 낮을수록 | 자주 가는 곳의 끝나는 시각 |
| `preference_match` | 선호 적합도 (콘텐츠 기반) | 높을수록 | 매물 특성 + 선호 |

- 입력하지 않은 정보의 기준은 자동으로 꺼지고 `notes` 에 남는다.
- **반영 비율:** 사용자가 화면 슬라이더로 기준별 비율(%)을 정한다 (`Lifestyle.weights_percent`, 합 100).
  - 0% 인 기준은 계산하지 않는다.
  - 입력 정보가 없어 계산 못 한 기준의 비율은 나머지 기준에 비율대로 나눈다 (`notes` 에 남김).
  - 비율을 비워 두면 계산 가능한 기준에 똑같이 나눈다.
- **자주 가는 지역:** 화면에서 `EWHA_AREAS`(이대역, 신촌역, 홍대입구역 등) 중에 고르고 용도·주간 횟수·끝나는 시각을 입력한다.
- 결과의 `strengths` / `weaknesses` 는 추천 근거 설명(LLM)에 넘길 데이터다.
- 매물 → 지역 이동시간은 `merge_travel_times()` 로 넣는다 (지금은 수동 입력, 이후 Route Tool 로 미리 계산).
- 매물 특성(`PropertyFeatures`)과 귀갓길 안전 데이터는 아직 없다. 공공데이터 정제(CUBIG) 뒤 채운다.
