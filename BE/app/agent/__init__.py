"""LLM(EXAONE 기본) + LangGraph 단일 에이전트 (2026-11-10, 2026-11-15 통합안 엔진으로 이전).

- 추천 점수·정렬은 통합안 엔진(app.scoring.recommend)이 정한다. LLM 은 조건 추출과 설명만 한다.
- schemas: 상태·조건·API 스키마 / tools: 서버가 검증하고 실행하는 도구 / llm: EXAONE·Gemini 와 테스트용 가짜 LLM
- graph: LangGraph 흐름 (추출 → 프로필 갱신 → 필수 항목 확인 → 도구 → 결과 검증 → 응답)
"""
