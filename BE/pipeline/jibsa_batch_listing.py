"""jibsa 설치 환경의 파이썬으로 실행하는 도우미: 매물 상세를 한 프로세스에서 차례로 조회한다.

jibsa CLI 는 실행할 때마다 시작에 약 4초가 들어 1,000건 넘게 확인하면 몇 시간이 걸린다.
CLI 와 같은 명령(`<소스> listing <id> --compact`)을 같은 프로세스 안에서 불러 시작 시간만 줄인다.
요청 간격·분당 상한은 jibsa 가 그대로 지킨다 (상태 파일 공유). 병렬로 부르지 않는다.

입력: 표준 입력 한 줄에 "<source> <id>" · 출력: 한 줄에 JSON {"source","id","out"} (out = CLI 가 낸 JSON 텍스트)
이 파일은 home_poc 패키지를 import 하지 않는다 (jibsa 환경에서 -I 로 실행).
"""

import contextlib
import io
import json
import sys

from jibsa.cli.app import app


def main() -> None:
    # -I 로 실행하면 PYTHONIOENCODING 이 무시된다 → 직접 UTF-8 로 맞춘다 (Windows 기본 cp949)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 2:
            continue
        source, lid = parts
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                app([source, "listing", lid, "--compact"], standalone_mode=False)
            except SystemExit:
                pass
            except Exception as e:  # noqa: BLE001 — 한 건 실패가 전체를 멈추지 않게
                err.write(json.dumps({"error": {"code": "EXCEPTION", "message": repr(e)}}))
        text = out.getvalue().strip() or err.getvalue().strip()
        sys.stdout.write(json.dumps({"source": source, "id": lid, "out": text}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
