# 챗봇 평가 케이스

`docs/rules-eval-plan.md` 4장의 L2·L3·L4 케이스. 케이스마다 `docs/chatbot-rules.md` 규칙 번호(`rules`)와
개발용/평가용(`split`: `dev` / `heldout`)이 붙어 있다. **평가용은 수정할 때 보지 않는다.**

| 파일 | 층 | 개수 | 평가용 |
|---|---|---|---|
| `cases/l2_extract.jsonl` | L2 조건 추출 (말 → 의도·생활 프로필) | 41 | 10 |
| `cases/l3_plan.jsonl` | L3 제외 계획 (말 → 확인 계획) | 25 | 7 |
| `cases/l4_dialog.jsonl` | L4 대화 (여러 턴, 답 문장까지) | 21 (26턴) | 6 |

케이스 원본은 `make_cases.py` 다. 고칠 때는 이 파일을 고치고 `uv run python eval/make_cases.py` 로 다시 만든다.

한 번 전체를 돌리면 LLM 을 약 123회(개발용만 약 91회) 부른다. Gemini 3.1 Flash-Lite 무료 한도를 하루 500회로 보고
(정확한 값은 AI Studio 의 Rate limit 화면) 하루에 전체 실행 3~4번이 들어가게 줄였다.

형식 검사 (LLM 을 부르지 않는다. `pytest` 에도 들어 있다):

```bash
uv run python scripts/check_eval_cases.py
```

규칙 번호가 규칙 문서에 있는지, 학교·건물·우선순위·의도가 고를 수 있는 값인지, L3 기대가 확인 도구에 있는
칸·시설·값인지, L3 정답 예시(`reference`)가 `ExclusionPlan` 검증과 기대를 통과하는지, 규칙 문서 R-508 기본값 숫자와
코드 `DEFAULT_RULES` 숫자가 같은지 본다.
코드나 규칙 문서를 바꾸면 케이스가 어긋났는지 여기서 바로 드러난다.

## 공통

- 금액 만 원, 면적 ㎡, 통학 편도 도보 분. 칸 이름은 `POST /recommend` 와 같다
  (`university`, `department_buildings`, `priorities`, `budget_manwon`, `deposit_budget_manwon`,
  `desired_commute_min`, `desired_area_m2`, `larger_area`, `timetable`).
- 판정은 **서버가 정리한 뒤의 생활 프로필** 기준이다. 예: "6평" → `desired_area_m2` 약 19.83 (`{"approx": 19.83, "tol": 0.2}`).
- 매물 수처럼 데이터가 바뀌면 달라지는 값은 기대에 적지 않는다.
- `from`: 예전 `agent_cases.jsonl` 의 어느 케이스에서 왔는지. v3 전용 개념(공강·1교시·주간 통학)이던 7개는
  같은 규칙을 시험하는 문장으로 바꿨다 (`c10 변환` 등).

## L2 기대 (`expect`)

| 키 | 뜻 |
|---|---|
| `intent` | 허용하는 의도 목록. `update` `recommend` `what_if` `revert` `compare` `explain` `exclude` `remove_exclusion` `out_of_scope` `greeting` |
| `profile` | 칸 → 기대값. `null` 은 비어 있어야 함, `"priorities.0"` 은 1순위만, `{"approx", "tol"}` 은 근삿값 |
| `others_unchanged` | `profile` 에 없는 칸은 `context.current` 그대로 |
| `unchanged` | 생활 프로필이 하나도 바뀌지 않음 (가정·설명·비교·범위 밖, R-1001) |
| `scenario` | 가정 턴에서 계산에 쓸 바뀐 값 |
| `includes` | 목록 칸에 이 값이 들어 있음 (예: 건물 줄임말 "포관" → 이화·포스코관) |
| `timetable` / `timetable_len` | 시간표 칸 전체 / 개수 |
| `missing_includes` | 다음에 물어야 할 칸에 들어 있어야 함 (`timetable.building` 은 강의동만 빠진 경우) |
| `not_set` | 값을 넣으면 안 되는 칸 (학년, R-302) |
| `has_exclusion_request` | 제외 조건으로 넘길 말이 있음(`true`) / 없음(`false`, R-310) |
| `unsupported_nonempty` | 지원하지 않는 요청이라 안내가 있어야 함 |
| `property_rank` | 설명할 매물 순위 |

`context`: `current`(지금 생활 프로필), `previous`(되돌릴 이전 조건), `exclusions`(지금 적용된 제외 조건),
`last_question`(앞 턴에서 물은 칸, R-604).

## L3 기대

조건마다 기준값 하나가 아니라 **빠져야 하는 값(`excludes`)과 남아야 하는 값(`keeps`)** 을 적는다.
"30m 이하 제외"와 "31m 미만 제외", `in ["원룸"]` 과 `not_in ["오피스텔"]` 처럼 같은 뜻의 다른 표현이 모두 통과한다.

```json
{"tool": "amenity", "amenity": "bar", "metric": "nearest_m", "excludes": [10, 30], "keeps": [31, 60]}
```

| 키 | 뜻 |
|---|---|
| `conditions` | 기대 조건 목록. 조건마다 `tool`(하나 또는 목록), `field`/`amenity`/`metric`, `keywords_any`(카카오), `excludes`, `keeps`, 또는 `any_of` |
| `exact` | 기대 조건 말고 다른 조건이 있으면 실패 |
| `no_conditions` | 데이터 조건이 하나도 없어야 함 (현장 확인·확인 불가·조건 변경) |
| `onsite` | `onsite_checks` 에 들어 있어야 할 항목 (부록 B) |
| `unsupported_nonempty` | `unsupported` 가 비어 있으면 실패 (R-504) |

`reference` 는 사람이 쓴 정답 예시로, 형식 검사에만 쓴다. 판정은 `eval.cases_check.match_plan()` 으로 한다.

## L4 기대 (턴마다)

| 키 | 뜻 |
|---|---|
| `intent`, `profile`, `unchanged`, `timetable_len` | L2 와 같음 (턴이 끝난 뒤 상태) |
| `exclusions_count` | 적용된 제외 조건 수 |
| `starts_with` | 답의 시작 (`"바뀐 것:"`, R-505) |
| `contains_all` / `contains_any` / `not_contains` | 답 문장 검사 (안내처, 금지 문구, 대리 추정 단어) |
| `numbers_grounded` | 답의 숫자가 모두 계산 결과에 있음 (R-801) |
| `max_questions`, `asks_for_any`, `asks_for_all` | 되묻기 개수와 대상 (R-601) |
| `recommended` | 이 턴에 추천 결과를 보여 줬는지 |
| `when_zero_left` / `when_some_left` | 제외 후 남는 매물이 0개일 때 / 아닐 때의 기대 (R-506) |
| `when_over_budget_in_top` | 상위 3개에 예산 초과 매물이 있을 때의 기대 (R-402) |

`safety: true` 인 케이스(거절·숫자·현장 추정 금지·노출 금지)는 통과 기준이 100%다.

## 실제 LLM 으로 실행 (`scripts/eval_agent.py`)

```bash
uv run python scripts/eval_agent.py --label 1차                 # 전체 (약 123회 호출, 분당 12회 → 약 15분)
uv run python scripts/eval_agent.py --label 수정 --split dev     # 고칠 때는 개발용만
uv run python scripts/eval_agent.py --resume eval/results/<파일>.jsonl   # 한도에 걸려 멈춘 곳부터
uv run python scripts/eval_agent.py --summary eval/results/<2차>.jsonl --before eval/results/<1차>.jsonl
```

- 결과는 `eval/results/<시각>_<이름>.jsonl`(원본: 답 문장·매물 주소가 들어가 커밋하지 않음)과 같은 이름의 `.md`(요약 표: 통과 수,
  실패한 케이스 id·검사 이름만, 커밋해도 됨). 요약 표의 '원인' 칸은 사람이 채운다 (프롬프트 / 규칙 문서 / 코드 / 기대값 / 데이터).
- L3·L4 는 `DATA_CSV_PATH` 의 실제 매물로 돈다. L4 의 매물 수에 따라 갈리는 판정(0개 남음, 예산 초과)은 두 경우를 모두 판정한다.
- 판정기 자체는 `tests/test_eval_runner.py` 가 확인한다: 정답만 내는 가짜 LLM 이면 87개 케이스가 모두 통과한다.
