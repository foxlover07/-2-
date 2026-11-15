"""챗봇 평가 실행 (실제 LLM 호출). BE 폴더에서:

    uv run python scripts/eval_agent.py --label 1차                       # 전체 (약 123회 호출, 분당 12회 → 약 15분)
    uv run python scripts/eval_agent.py --label 수정중 --split dev         # 개발용만 (평가용은 고칠 때 보지 않는다)
    uv run python scripts/eval_agent.py --layers L3 --ids e01,e05         # 일부만
    uv run python scripts/eval_agent.py --resume eval/results/<파일>.jsonl  # 한도에 걸려 멈춘 곳부터
    uv run python scripts/eval_agent.py --summary eval/results/<2차>.jsonl --before eval/results/<1차>.jsonl  # 전후 표만

- 모델은 BE/.env.local 의 GEMINI_MODEL (temperature 0). --provider exaone 으로 EXAONE 만 쓸 수도 있다.
- 매물 데이터는 DATA_CSV_PATH (L3·L4 에서 씀). 키는 출력하지 않는다.
- 결과: eval/results/<시각>_<이름>.jsonl (원본, 커밋 안 함) + 같은 이름 .md (요약 표, 커밋해도 됨).
- Gemini 무료 하루 한도는 미국 태평양 자정(한국 오후 4~5시)에 초기화된다. 한도에 걸리면 멈추고 이어 하기 명령을 알려 준다.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_DIR))

from app.agent import rules  # noqa: E402
from app.agent.llm import LLMUnavailable, exaone_from_env, gemini_from_env  # noqa: E402
from app.data import _csv_paths, load_properties, load_travel_times  # noqa: E402
from eval.cases_check import check_all, estimate_calls  # noqa: E402
from eval.runner import LAYERS, Paced, load_rows, run, summary_md  # noqa: E402

RESULTS = BE_DIR / "eval" / "results"


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=BE_DIR, capture_output=True, text=True,
                              timeout=5).stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--layers", default="L2,L3,L4")
    ap.add_argument("--split", choices=["all", "dev", "heldout"], default="all")
    ap.add_argument("--ids", default="")
    ap.add_argument("--rpm", type=float, default=12, help="분당 최대 호출 수 (무료 한도 15보다 낮게)")
    ap.add_argument("--provider", choices=["gemini", "exaone"], default="gemini")
    ap.add_argument("--resume", default="")
    ap.add_argument("--summary", default="", help="실행하지 않고 jsonl 로 요약 표만 만든다")
    ap.add_argument("--before", default="", help="전후 비교할 이전 실행 jsonl")
    a = ap.parse_args()

    before = load_rows(Path(a.before)) if a.before else None
    if a.summary:
        path = Path(a.summary)
        md = summary_md(load_rows(path), {"label": path.stem}, before)
        path.with_suffix(".md").write_text(md, encoding="utf-8")
        print(md)
        return 0

    errs = check_all()
    if errs:
        print("케이스 형식 오류가 있어 실행하지 않습니다:", *errs, sep="\n  ")
        return 1
    layers = [x.strip() for x in a.layers.split(",") if x.strip() in LAYERS]
    ids = [x for x in a.ids.split(",") if x]
    try:
        base = gemini_from_env() if a.provider == "gemini" else exaone_from_env()
    except LLMUnavailable as e:
        print(f"LLM 설정이 없어 실행하지 않습니다: {e}")
        return 1
    llm = Paced(base, a.rpm)

    props = load_properties()
    travel = load_travel_times(props)
    data = lambda: (props, travel)  # noqa: E731

    RESULTS.mkdir(parents=True, exist_ok=True)
    if a.resume:
        out = Path(a.resume)
        done = {(r["layer"], r["id"]) for r in load_rows(out) if r.get("error") is None}
    else:
        out = RESULTS / f"{datetime.now():%Y%m%dT%H%M}_{a.label}.jsonl"
        done = set()
    est = estimate_calls(None if a.split == "all" else a.split)
    print(f"모델 {base.model_name} · 규칙 문서 v{rules.version()} · 커밋 {_commit()} · 매물 {len(props)}개 · "
          f"호출 어림 {est}회 (분당 {a.rpm:g}회) → {out.name}")
    finished = run(llm, layers, out, split=a.split, ids=ids, data=data, done=done)
    meta = {"label": a.label, "모델": f"{base.provider} {base.model_name} (temperature 0)", "규칙 문서": f"v{rules.version()}",
            "커밋": _commit(), "매물 데이터": ", ".join(p.name for p in _csv_paths()) + f" ({len(props)}개)",
            "범위": f"{','.join(layers)} / {a.split}", "실제 호출": f"{llm.calls}회 (재시도 제외)",
            "상태": "완료" if finished else f"한도로 멈춤 — 이어 하기: --resume {out}"}
    md = summary_md(load_rows(out), meta, before)
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print("\n" + md)
    return 0 if finished else 2


if __name__ == "__main__":
    raise SystemExit(main())
