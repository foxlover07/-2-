"""에이전트 조건 추출 평가 실행 (실제 LLM 호출 — 케이스 1개 = 호출 1회 + 재시도). 실행 전 사용자 승인.

실행 (BE 에서):
    ../home/Scripts/python.exe scripts/eval_agent.py --provider exaone                 # .env.local 의 EXAONE_MODEL
    ../home/Scripts/python.exe scripts/eval_agent.py --provider gemini --models gemini-3.5-flash-lite,gemini-3.1-flash-lite
    ../home/Scripts/python.exe scripts/eval_agent.py --ids c05,c06 --limit 5
    ../home/Scripts/python.exe scripts/eval_agent.py --provider gemini --sleep 5     # 분당 한도(429)가 있으면 간격을 둔다

- 모델마다 따로 평가한다 (대체 모델 없이 그 모델만). 결과: BE/eval/results/<시각>_<모델>.json (커밋 안 함).
- 실제 매물 데이터(DATA_CSV_PATH)로 건물 이름을 맞춘다. 키는 출력하지 않는다.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_DIR))

from app.agent.evaluate import load_cases, run  # noqa: E402
from app.agent.llm import ExaoneLLM, GeminiLLM, LLMUnavailable  # noqa: E402
from app.config import _read  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=(_read("LLM_PROVIDER") or "exaone"), choices=["exaone", "gemini"])
    ap.add_argument("--models", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.0, help="케이스 사이 대기(초). 분당 호출 한도가 있는 무료 키용 (예: Gemini 4~5)")
    a = ap.parse_args()
    prefix = a.provider.upper()
    key = _read(f"{prefix}_API_KEY")
    models = [m.strip() for m in (a.models or _read(f"{prefix}_MODEL") or "").split(",") if m.strip()]
    base_url = (_read("EXAONE_BASE_URL") or "").strip()
    if not key or not models or (a.provider == "exaone" and not base_url):
        raise LLMUnavailable(f"{prefix}_API_KEY 와 모델(--models 또는 {prefix}_MODEL)"
                             + (", EXAONE_BASE_URL" if a.provider == "exaone" else "") + " 이 필요합니다.")

    def paced(llm):
        if not a.sleep:
            return llm
        import time

        class Paced:
            def extract(self, message, context):
                time.sleep(a.sleep)
                return llm.extract(message, context)
        return Paced()

    def make(model: str):
        if a.provider == "gemini":
            return GeminiLLM(key, [model])
        mode = (_read("EXAONE_JSON_MODE") or "prompt").strip()
        return ExaoneLLM(key, base_url, [model], json_mode="json_schema" if mode == "json_schema" else "prompt",
                         reasoning_effort=(_read("EXAONE_REASONING_EFFORT") or "").strip() or None)
    cases = load_cases()
    if a.ids:
        want = set(a.ids.split(","))
        cases = [c for c in cases if c["id"] in want]
    if a.limit:
        cases = cases[: a.limit]
    out_dir = BE_DIR / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    for model in models:
        res = run(paced(make(model)), cases)
        res["model"] = model
        res["provider"] = a.provider
        path = out_dir / f"{ts}_{a.provider}_{model.replace('/', '_')}.json"
        path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n== {model}: 케이스 {res['case_accuracy']:.0%} · 의도 {res['intent_accuracy']:.0%} · "
              f"항목 {res['check_accuracy']:.0%} · 오류 {res['errors']}건 ({res['cases']}개) → {path.name}")
        for r in res["rows"]:
            if not r["ok"]:
                bad = [k for k, v in r["checks"].items() if not v] or [r.get("error", "")]
                print(f"  ✗ {r['id']} {r['message'][:30]} → {', '.join(bad)} | {json.dumps(r.get('extraction', {}), ensure_ascii=False)[:160]}")


if __name__ == "__main__":
    main()
