"""평가 케이스 형식 검사 (LLM 을 부르지 않는다).

    uv run python scripts/check_eval_cases.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.cases_check import check_all, estimate_calls, summary  # noqa: E402


def main() -> int:
    for name, s in summary().items():
        extra = f", 대화 턴 {s['턴']}" if name.startswith("l4") else ""
        print(f"{name}: {s['전체']}개 (평가용 {s['평가용']}{extra})")
    print(f"LLM 호출 어림: 전체 {estimate_calls()}회 (개발용만 {estimate_calls('dev')}회)")
    errs = check_all()
    for e in errs:
        print("  ✗", e)
    print("문제 없음" if not errs else f"문제 {len(errs)}개")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
