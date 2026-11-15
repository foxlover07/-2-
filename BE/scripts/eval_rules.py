"""규칙 기반 3단계 평가 실행 (실제 LLM 호출 — 실행 전 사용자 승인). app.agent.rules_eval 참고.

실행 (BE 에서):
    ../home/Scripts/python.exe -X utf8 scripts/eval_rules.py --layers L2,L3 --split dev --provider gemini --sleep 4
    ../home/Scripts/python.exe -X utf8 scripts/eval_rules.py --layers L4 --ids d01,d02

호출 수 (대략): L2 케이스당 1회, L3 1회, L4 턴당 1~3회(추출·계획·설명). 전체 약 130회, 개발용만 약 95회.
결과: BE/eval/results/<시각>_rules_<모델>.json (커밋 안 함). 키는 출력하지 않는다.
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

BE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_DIR))

from app.agent.llm import ExaoneLLM, GeminiLLM, LLMUnavailable  # noqa: E402
from app.agent.rules_eval import run_case, save, select, summarize  # noqa: E402
from app.config import _read  # noqa: E402


class Paced:
    """분당 호출 한도가 있는 무료 키용: 호출마다 sleep 초 쉰다."""

    def __init__(self, llm, sleep: float):
        self.llm, self.sleep = llm, sleep

    def __getattr__(self, name):
        fn = getattr(self.llm, name)
        if name not in ("extract", "plan", "explain"):
            return fn

        def call(*a, **k):
            time.sleep(self.sleep)
            return fn(*a, **k)
        return call


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=(_read("LLM_PROVIDER") or "gemini"), choices=["gemini", "exaone"])
    ap.add_argument("--model", default="")
    ap.add_argument("--layers", default="L2,L3,L4")
    ap.add_argument("--split", default="all", choices=["all", "dev", "heldout"])
    ap.add_argument("--ids", default="")
    ap.add_argument("--sleep", type=float, default=0.0)
    a = ap.parse_args()
    prefix = a.provider.upper()
    key = _read(f"{prefix}_API_KEY")
    model = (a.model or _read(f"{prefix}_MODEL") or "").strip()
    if not key or not model:
        raise LLMUnavailable(f"{prefix}_API_KEY 와 모델(--model 또는 {prefix}_MODEL) 이 필요합니다.")
    if a.provider == "gemini":
        llm = GeminiLLM(key, [model])
    else:
        mode = (_read("EXAONE_JSON_MODE") or "prompt").strip()
        llm = ExaoneLLM(key, (_read("EXAONE_BASE_URL") or "").strip(), [model],
                        json_mode="json_schema" if mode == "json_schema" else "prompt",
                        reasoning_effort=(_read("EXAONE_REASONING_EFFORT") or "").strip() or None)
    llm = Paced(llm, a.sleep) if a.sleep else llm
    rows = []
    for layer in [x.strip().upper() for x in a.layers.split(",") if x.strip()]:
        for case in select(layer, a.split, [i for i in a.ids.split(",") if i]):
            r = run_case(llm, layer, case)
            rows.append(r)
            bad = [k for k, v in r["checks"].items() if not v] or ([r["error"]] if "error" in r else [])
            print(f"{'✓' if r['ok'] else '✗'} {layer} {r['id']} {'' if r['ok'] else ', '.join(bad)[:150]}")
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = BE_DIR / "eval" / "results" / f"{ts}_rules_{a.provider}_{model.replace('/', '_')}.json"
    save(rows, path, {"provider": a.provider, "model": model, "split": a.split, "layers": a.layers})
    print(summarize(rows), "→", path.name)


if __name__ == "__main__":
    main()
