"""챗봇 LLM 이 실제로 응답하는지 확인 (조건 추출 1회 + 설명 1회). 키는 출력하지 않는다.

실행 (BE 에서):
    uv run python scripts/check_llm.py                      # .env.local 의 LLM_PROVIDER 대로 (서버와 같은 설정)
    uv run python scripts/check_llm.py --provider exaone    # EXAONE 만 (Gemini 대체 없이)
    uv run python scripts/check_llm.py --ping             # 짧은 질문으로 추론 깊이별 속도만 (사이 20초 대기)
    uv run python scripts/check_llm.py --bench            # 실제 조건 추출을 low/high 로 한 번씩 (사이 20초 대기)
    uv run python scripts/check_llm.py --limits           # EXAONE 하루·분·초 한도와 남은 횟수 (호출 1회)
    uv run python scripts/check_llm.py --compare          # Gemini vs EXAONE 속도 비교 (3회, EXAONE 호출 6회)
    uv run python scripts/check_llm.py --provider exaone --effort high --timeout 90

시도마다 걸린 시간·오류 종류·HTTP 상태, 추론 토큰 수를 같이 보여 준다 (느린 원인 진단용).
"""

import argparse
import json
import sys
import time
from pathlib import Path

BE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BE_DIR))

from app.agent import llm as L  # noqa: E402
from app.agent.llm import LLMError, LLMUnavailable  # noqa: E402

MESSAGE = "이대 다니고 월세랑 관리비 합쳐서 70만 원 이하로 추천해 줘. 공대 수업이 많아."
FACTS = {  # 예시 사실 (실제 매물 아님)
    "mode": "explain",
    "result": {"university": "이화여자대학교", "candidates": 120, "top": [
        {"rank": 1, "address": "예시동 1", "type": "원룸", "deposit_manwon": 1000.0,
         "vs_target": ["유지비 월 62만 원(상한보다 8만 원 쌈)", "편도 도보 평균 9분(희망보다 6분 가까움)", "면적 21㎡(희망보다 1㎡ 넓음)"]}]},
}


def exaone(effort: str | None, timeout: float | None) -> L.ExaoneLLM:
    base = L.exaone_from_env()
    llm = L.ExaoneLLM(L._read("EXAONE_API_KEY") or "", base.models, base_url=base._base_url,
                      reasoning_effort=effort or base._reasoning_effort,
                      timeout_s=timeout or L.EXAONE_TIMEOUT_S)
    if timeout:
        llm.total_budget_s = timeout * 1.5
    return llm


def report(llm) -> None:
    for x in (llm.text if isinstance(llm, L.ChainLLM) else [llm]):
        for a in getattr(x, "attempts", []):
            print(f"    · {getattr(x, 'provider', '?')} 시도: {a}")
        for m, chat in getattr(x, "_chats", {}).items():
            if getattr(chat, "last_usage", None):
                print(f"    · 마지막 성공 호출 {m}: {chat.last_usage}")


def bench(effort_list: list[str], timeout: float | None, gap: float) -> None:
    for i, effort in enumerate(effort_list):
        if i:
            time.sleep(gap)
        llm = exaone(effort, timeout or 120)
        t = time.monotonic()
        try:
            out = llm.extract(MESSAGE, {"current_profile": {}})
            print(f"✓ 조건 추출 effort={effort}: {time.monotonic() - t:.1f}초 → {out.model_dump(mode='json', exclude_defaults=True)}")
        except LLMError as e:
            print(f"✗ 조건 추출 effort={effort}: {e.kind}, {time.monotonic() - t:.1f}초")
        report(llm)


def limits() -> None:
    llm = exaone(None, None)
    chat = llm._chat(llm.models[0])
    try:
        chat.complete([{"role": "user", "content": "'안녕'에 한 단어로만 답해."}])
        print("EXAONE 한도:", getattr(chat, "last_limits", None) or "(헤더 없음)")
    except Exception as e:  # noqa: BLE001
        print(f"✗ {type(e).__name__} status={getattr(e, 'status_code', None)} 한도: {L._limit_headers(e)}")


def compare(rounds: int, gap: float) -> None:
    import statistics

    llms = {"Gemini": L.gemini_from_env(), "EXAONE": exaone(None, 120)}  # EXAONE 은 실제 걸리는 시간을 재려고 제한 120초
    tasks = {"조건 추출": lambda llm: llm.extract(MESSAGE, {"current_profile": {}}),
             "설명 생성": lambda llm: llm.explain("왜 1위야?", FACTS)}
    rec: dict[tuple[str, str], list[float | None]] = {(p, t): [] for p in llms for t in tasks}
    for r in range(rounds):
        for tname, task in tasks.items():
            for pname, llm in llms.items():
                t = time.monotonic()
                try:
                    task(llm)
                    rec[(pname, tname)].append(time.monotonic() - t)
                    tag = f"{time.monotonic() - t:.1f}초 ({llm.last_model})"
                except LLMError as e:
                    rec[(pname, tname)].append(None)
                    tag = f"실패 {e.kind} ({time.monotonic() - t:.1f}초) {getattr(llm, 'attempts', [])[-1:]}"
                print(f"[{r + 1}/{rounds}] {tname} {pname}: {tag}", flush=True)
                if pname == "EXAONE":
                    time.sleep(gap)  # 초당 한도·혼잡 피하기
    print("\n제공자   작업        중앙값   성공")
    turn: dict[str, float] = {}
    for (p, t), xs in rec.items():
        ok = [x for x in xs if x is not None]
        med = statistics.median(ok) if ok else float("nan")
        turn[p] = turn.get(p, 0) + (med if ok else float("inf"))
        print(f"{p:7}  {t:8}  {med:6.1f}초  {len(ok)}/{len(xs)}")
    chat = llms["EXAONE"]._chats.get(llms["EXAONE"].models[0])
    print("\nEXAONE 한도:", getattr(chat, "last_limits", None) or "(헤더 없음)")
    best = min(turn, key=turn.get)
    print(f"한 턴(추출+설명) 중앙값: " + ", ".join(f"{p} {v:.1f}초" for p, v in turn.items()))
    print(f"→ 더 빠른 쪽: {best}  (BE/.env.local 에 LLM_PROVIDER={best.lower()})")


def ping(effort_list: list[str], timeout: float | None, gap: float) -> None:
    for i, effort in enumerate(effort_list):
        if i:
            time.sleep(gap)  # 한도 피하기
        llm = exaone(effort, timeout)
        chat = llm._chat(llm.models[0])
        t = time.monotonic()
        try:
            out = chat.complete([{"role": "user", "content": "'안녕'에 한 단어로만 답해."}])
            print(f"✓ ping effort={effort}: {time.monotonic() - t:.1f}초 → {out[:30]!r} {chat.last_usage}")
        except Exception as e:  # noqa: BLE001
            print(f"✗ ping effort={effort}: {time.monotonic() - t:.1f}초, {type(e).__name__} "
                  f"status={getattr(e, 'status_code', None)} {L._limit_headers(e)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["env", "exaone", "gemini"], default="env")
    ap.add_argument("--effort", choices=["low", "high", "max"], default=None)
    ap.add_argument("--timeout", type=float, default=None, help="EXAONE 호출 한 번의 제한 시간(초)")
    ap.add_argument("--ping", action="store_true", help="짧은 질문으로 low/high 속도만 잰다")
    ap.add_argument("--bench", action="store_true", help="실제 조건 추출로 low/high 속도를 잰다")
    ap.add_argument("--gap", type=float, default=20, help="--ping/--bench 호출 사이 대기(초)")
    ap.add_argument("--limits", action="store_true", help="EXAONE 한도·남은 횟수 확인 (호출 1회)")
    ap.add_argument("--compare", action="store_true", help="Gemini vs EXAONE 속도 비교")
    ap.add_argument("--rounds", type=int, default=3)
    a = ap.parse_args()
    try:
        efforts = [a.effort] if a.effort else ["low", "high"]
        if a.ping:
            return ping(efforts, a.timeout, a.gap)
        if a.bench:
            return bench(efforts, a.timeout, a.gap)
        if a.limits:
            return limits()
        if a.compare:
            return compare(a.rounds, min(a.gap, 5))
        llm = {"env": L.llm_from_env, "gemini": L.gemini_from_env,
               "exaone": lambda: exaone(a.effort, a.timeout)}[a.provider]()
    except LLMUnavailable as e:
        sys.exit(f"[설정 없음] {e}")
    print(f"LLM: {type(llm).__name__} ({getattr(llm, 'model_name', '?')})")
    for name, call in (("조건 추출", lambda: llm.extract(MESSAGE, {"current_profile": {}})),
                       ("설명 생성", lambda: llm.explain("왜 1위야?", FACTS))):
        t = time.monotonic()
        try:
            out = call()
            shown = json.dumps(out.model_dump(mode="json", exclude_defaults=True), ensure_ascii=False) \
                if hasattr(out, "model_dump") else out
            print(f"✓ {name} — {getattr(llm, 'last_model', None)}, {time.monotonic() - t:.1f}초\n{shown}")
        except LLMError as e:
            print(f"✗ {name}: {e} (종류 {e.kind}, {time.monotonic() - t:.1f}초)")
        report(llm)
        print()
        time.sleep(a.gap)


if __name__ == "__main__":
    main()
