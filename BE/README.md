# BE — AI 주거 추천 서비스 백엔드

FastAPI 백엔드. 매물·도보시간·부가정보(CSV·JSON 파일)를 읽어 추천, AI 상담 챗봇, 의사결정 분석, 매물 상세 정보, 시간표 사진 읽기 API를 제공한다.
설계와 결정 이력은 [`docs/PROJECT.md`](../docs/PROJECT.md)가 기준이다 (코드와 다르면 코드가 우선).

## 준비

Python 3.13, [uv](https://docs.astral.sh/uv/)가 필요하다. `BE/` 폴더에서:

```bash
uv sync                  # 기본
uv sync --group ocr      # 시간표 사진 OCR(EasyOCR, torch 포함 1GB+)까지. 없으면 /timetable/ocr 이 ocr_unavailable 로 답한다
cp .env.example .env.local
```

키·경로는 `.env.local` 에만 넣는다 (`.env.example` 에는 이름만, 커밋 금지). 나머지는 `.env.example` 참고 (아래 `TIMETABLE_LLM_FALLBACK`, `DATA_BUNDLE_KEY` 는 배포 서버 설정이라 `.env.example` 에 없다). 자주 쓰는 것:

| 키 | 설명 |
| --- | --- |
| `CORS_ORIGINS` | 허용 Origin, 쉼표 구분 (기본 `http://localhost:5173`) |
| `DATA_CSV_PATH` | 매물 CSV, 쉼표로 여러 개 (기본 `../DB/poc_ewha.csv` 샘플) |
| `WALK_TABLE_PATH` | 매물 × 건물 도보시간 표, 쉼표로 여러 개 (없으면 매물 CSV 의 spot1~4 만) |
| `LLM_PROVIDER`, `LLM_FALLBACK_PROVIDERS` | 챗봇 LLM 순서 (지금 `gemini` → `exaone`) |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini |
| `EXAONE_API_KEY`, `EXAONE_BASE_URL`, `EXAONE_MODEL` | LG EXAONE (OpenAI 호환 API) |
| `KAKAO_REST_API_KEY` | 장소 검색·도보 경로 (건물·자주 가는 곳 도보시간, 장소 제외 조건) |
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 (실거래가·건축물대장·상가정보 수집) |
| `YOUTHCENTER_API_KEY` | 온통청년 정책 수집 |
| `TIMETABLE_LLM_FALLBACK` | `on` 이면 OCR 이 없는 서버에서 시간표 글자를 Gemini 로 읽는다 (시각은 OpenCV 격자) |
| `DATA_BUNDLE_KEY` | 배포용: 암호화 데이터 묶음(`data/deploy_bundle.bin`)을 푸는 키. 없으면 묶음을 쓰지 않는다 |

실거래·상가정보·건축물대장·역/공원·정책 파일은 경로를 비우면 `DB/staging` 에서 가장 최근 파일을 자동으로 고른다 (`MARKET_TX_PATH`, `STORES_PATH`, `BUILDING_TITLES_PATH`, `POI_PATH`, `POLICIES_PATH` 로 바꿀 수 있음).

## 실행

```bash
uv run uvicorn app.main:app --port 8011
```

- API 문서: http://localhost:8011/docs
- FE 는 `VITE_API_URL=http://localhost:8011` 로 붙는다.

### API

| 경로 | 내용 |
| --- | --- |
| `GET /health` | 상태 확인 |
| `GET /properties`, `GET /properties/{id}` | 매물 목록·상세 |
| `POST /recommend` | 추천 (아래 "추천") |
| `GET /recommend/buildings` | 대학별 학교 건물 목록·도보 데이터 범위 |
| `POST /recommend/building-walks` | 도보시간 없는 매물 × 건물을 카카오로 계산 (하루 상한) |
| `POST /analysis/pareto` · `tradeoff` · `sensitivity` · `relaxation` | 의사결정 분석 |
| `GET /market/compare/{id}` | 주변 실거래 시세 비교 |
| `GET /market/amenities/{id}` | 편의시설·역·정류장·공원 |
| `GET /market/building/{id}` | 건축물대장 |
| `GET /market/contract-check/{id}` | 계약 전 확인 항목 |
| `GET /market/related-policies/{id}` | 관련 정책 안내 (자격 판정 없음) |
| `POST /agent/chat` | AI 상담 챗봇 한 턴 |
| `POST /agent/timetable-image`, `POST /timetable/ocr` | 시간표 사진 → 수업 칸 미리보기 (사진 저장 안 함) |

## 테스트

```bash
uv run pytest
```

LLM 실제 호출 평가(유료·한도 사용, 실행 전 승인): `scripts/eval_agent.py`, `scripts/eval_rules.py`. 결과는 `eval/results/`(커밋 안 함).

## 구조

```
BE/
├── app/
│   ├── main.py              # FastAPI 앱, CORS, /health, 배포 묶음 풀기
│   ├── models.py            # Pydantic Property 모델 (FE/src/types/property.ts 와 1:1)
│   ├── data.py              # 매물 CSV·도보시간 표 로딩 (빈 값 → null)
│   ├── config.py            # 환경 변수 읽기
│   ├── scoring.py           # 추천 엔진 (필터, 요소별 점수, 종합 점수, 비추천 사유)
│   ├── scoring_config.py    # 추천 설정값 (통합안 6장)
│   ├── exclusions.py        # 제외 조건: 확인 계획 형식 + 확인 도구 실행
│   ├── decision/            # 의사결정 분석 (pareto, sensitivity, relaxation)
│   ├── agent/               # 챗봇: LangGraph 그래프, LLM 연결(Gemini→EXAONE), 규칙·검증, 평가
│   ├── buildings.py · building_lookup.py · building_walks.py   # 건물 이름 정규화, 목록 밖 건물 찾기, 그때 도보 계산
│   ├── frequent_places.py · place_search.py                   # 자주 가는 곳, 장소 검색
│   ├── market.py · amenities.py · poi.py · building_register.py # 실거래·편의시설·역/공원·건축물대장
│   ├── listing_status.py    # 거래 끝난 매물 빼기
│   ├── policy.py            # 정책 안내
│   ├── timetable_ocr.py     # 시간표 사진 (OpenCV 격자 + EasyOCR)
│   ├── deploy_bundle.py     # 배포용 암호화 데이터 묶음
│   ├── tools/kakao.py       # Kakao API (장소 검색·도보 경로·주소)
│   └── routers/             # properties · recommend · analysis · market · agent · timetable
├── pipeline/                # 데이터 수집·가공 (python -m pipeline.<이름>, docs/PROJECT.md 5장)
├── scripts/                 # ask.py(LLM 호출 실험) · eval_agent.py · eval_rules.py
├── eval/                    # 챗봇 평가 케이스
├── data/deploy_bundle.bin   # 배포용 암호화 데이터
└── tests/                   # pytest
```

## 추천 (`app/scoring.py`)

팀의 「추천 시스템 통합안」(2026-11-13 회의 확정안, 상세는 통합 문서 `docs/PROJECT.md` 3장)을 구현한다.
화면 추천과 챗봇 추천이 같은 엔진을 쓴다. v3 카테고리 추천(`categories.py`, `GET /recommend/categories`)과 `POST /recommend/v2`는 이 통합안으로 합쳐 지웠다.

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

- **이동 시간**은 학과 건물 0.4 · 자주 가는 곳 0.4 · 시간표 0.2. 부분마다 편도 평균에 규칙을 적용한 다음 합친다. 자주 가는 곳은 챗봇에서 입력했을 때만 쓰고(추천 상위 매물만 카카오 도보 경로로 계산), 없거나 시간표가 없으면 그 비중을 나머지에 나눈다.
- 예 (희망 15분, 상한 75만 원, 희망 20㎡): 이동 0분 57.5 · 25분 40 / 유지비 45만 80 · 85만 30 / 면적 16㎡ 30 · 30㎡ 좋음 75, 싫음 37.5.
- 교환비는 매물 1,175개 회귀분석의 시장 가격으로 정했다 (통합안 3.2장). 학생이 느끼는 통학의 무게보다 작을 수 있다.

### 종합과 결과

- 종합 점수 = `0.55 × 1순위 + 0.30 × 2순위 + 0.15 × 3순위`. 점수 높은 순 → 유지비 낮은 순 → 매물 ID 순.
- **비추천 사유:** 다른 5가지 1·2순위 조합 중 어디서든 상위 3위 안인데 지금 상위 3위(`displayed_top_n`) 밖인 매물, 최대 3개. 지금 1위와 비교해 가중 점수 차이가 가장 큰 요소를 사유로 쓴다. 이동 시간이 사유면 세부 부분 중 점수가 가장 낮은 부분의 값을 쓴다.
- **기준 대비 값:** `vs_target`(희망보다 몇 분 / 예산보다 몇 만 원 / 희망보다 몇 ㎡). 기준을 맞추면 50점이라 점수 자체는 낮아 보일 수 있어, 화면에는 이 값을 보여 준다 (통합안 3.6장).
- **학과 → 건물:** `DEPARTMENT_BUILDINGS`는 이대 **예시값 3개뿐이라 팀 확인이 필요하다.** 다른 대학과 표에 없는 학과는 `department_buildings`로 직접 입력한다.
- **학년:** 받기만 하고 계산에는 쓰지 않는다.

### 챗봇 제외 조건 (`app/exclusions.py`)

**확인 계획**을 `exclusions`로 넣으면 코드가 매물마다 확인한다. 챗봇은 말에서 장소 기호(`place_prefs`)와 그 밖의 기호(`exclusion_request`)를 뽑고, 계획 단계에서 이 형식으로 바꾼다. `POST /recommend`에 계획 JSON을 직접 넣어 테스트할 수도 있다.

| 도구 | 하는 일 | 필요한 칸 |
| --- | --- | --- |
| `nearby_count` | 반경 N m 안 장소 개수 (키워드 여러 개는 장소 ID로 중복 제거) | `keywords` 또는 `category`, `radius_m` |
| `nearest_distance` | 가장 가까운 장소까지 거리. 반경(기본 1000m) 안에 없으면 '없음' | `keywords` 또는 `category` |
| `listing_field` | 매물 칸: 면적·보증금·월세·관리비·월 고정 주거비, 층 종류(반지하·지하·옥탑·1층·2층 이상), 층 숫자, 건물 전체 층수, 엘리베이터 없이 오르는 층, 방 구조, 매물 유형 | `field` |
| `building_field` | 건축물대장: 건물 연식, 사용승인 연도, 용도(주거용·근린생활시설·업무시설), 지상 층수, 승강기, 주차, 세대 수 | `field` |
| `amenity` | 미리 계산한 주변 시설(편의점·마트·세탁소·코인빨래방·약국·병원·술집·노래방, 추가 예정: 헬스장·공원·지하철역·버스정류장)의 가장 가까운 거리 또는 500m 안 개수 | `amenity`, `metric` |

- 숫자 칸은 `>`·`>=`·`<`·`<=`·`==`·`!=`, 종류 칸(층 종류·방 구조·용도)은 `in`·`not_in`과 값 목록으로 비교한다. 칸 목록과 고를 수 있는 값은 `tool_catalog()`가 코드에서 만든다(LLM 프롬프트용).
- 칸 값을 모르는 매물은 빼지 않고 notes에 "확인 못 함 N개"로 남긴다. 건축물대장 칸은 `bld_status`가 `matched`인 매물만 쓴다.
- 주변 시설 거리가 비어 있고 500m 안 개수가 0이면 '500m 안에 없음'으로 본다. 500m를 넘는 기준(예: 1km)은 이 경우 판단하지 않는다.
- **확인 질문 없이 바로 적용한다.** 답변 맨 앞에 '바뀐 것'(`ExclusionPlan.applied_text(preview)`, 서버가 조건에서 만든 문장)을 붙인다. `preview_plan()`으로 조건마다 빠지는 매물 수와 남는 수를 계산해 함께 보여 준다(외부 호출 없음, 카카오 조건은 실시간 검색). **적용하면 남는 매물이 0개일 때만** 적용하지 않고 어느 조건을 풀지 묻는다(`blocked_text`).
- **매물에서 직접 봐야 하는 것(방음·곰팡이·결로·채광·수압·배수·벌레·외풍·냄새)은 데이터로 추정하지 않는다.** 이런 말이 조건 이름에 들어가면 서버가 계획을 거절하고, `onsite_checks`(정해진 항목 `ONSITE_CHECKS`)로 집 볼 때 확인할 것을 안내한다. 데이터로 못 보는 기호는 `unsupported`(빼지 않음).
- 시설 칸이 없는 시설은 `tool_catalog(props)`에 나오지 않는다(데이터가 생기면 자동으로 쓰인다).
- 층·방 구조·주변 시설·건축물대장 칸은 통합 매물 CSV(`integrated_listings.csv`)에만 있다. PoC CSV와 대학별 추천 매물 CSV(`*_candidates_*.csv`)에서는 비어 있어 '확인 못 함'이 된다.

```json
{
  "source_text": "밤에 시끄러우면 잠을 못 자요",
  "conditions": [{"tool": "nearby_count", "label": "술집", "keywords": ["술집", "호프", "클럽"],
                  "radius_m": 150, "exclude_if": {"op": ">", "value": 2}}],
  "unsupported": [],
  "confirm_message": "주변 150m 안에 술집이 3곳 이상인 매물을 뺄게요. 괜찮으세요?"
}
```

- 장소 도구는 카카오 장소 검색을 쓴다 (`KAKAO_REST_API_KEY`). 카카오 결과는 저장이 금지라 프로세스 메모리 캐시만 쓴다. 주변 시설 8종은 `amenity`를 먼저 쓴다. 키가 없으면 장소 조건은 확인하지 않고 notes로 안내한다.
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
- **매물 좌표:** 레포의 PoC 매물 10개는 좌표가 비어 있다 (수집 데이터는 `.env.local` 경로로 지정).
- **챗봇:** 동작하지만 아직 다듬는 중이다. 2026-11-16 생활 시나리오 테스트에서 희망 통학 시간·면적이 없으면 추천 단계로 가지 못했고, '술집 많은 곳' 같은 기준(개수)은 LLM 이 정한다.
- **교환비:** 시장 가격으로 정한 값이라 학생이 느끼는 통학의 무게보다 작을 수 있다. "A가 B보다 위여야 한다" 형식의 테스트 케이스로 확인한다.
- **도로·소음:** 카카오 장소 검색으로는 확인할 수 없다. 추후 도구로 추가한다.
- **대중교통 시간:** 지금은 도보만 쓴다.
- **관리비 정보가 없는 매물:** 지금은 "월세 또는 관리비 정보 없음"으로 제외한다.
