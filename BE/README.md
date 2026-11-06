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
│   └── routers/
│       └── properties.py    # GET /properties, GET /properties/{property_id}
└── tests/
    └── test_properties.py
```
