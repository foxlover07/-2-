"""챗봇 규칙 문서(docs/chatbot-rules.md)의 장을 읽어 프롬프트에 넣는다 (agent-integrated 에서 가져옴, 2026-11-16).

- 규칙 문서가 원본이다. 문서를 고치면 프롬프트도 같이 바뀐다.
- 이 브랜치에서 프롬프트에 넣는 장: 5장(제외 계획), 7장(조건 추출의 범위 밖 판단). 다른 장은 설계·평가 기준.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config import REPO_ROOT

RULES_DOC = REPO_ROOT / "docs" / "chatbot-rules.md"


@lru_cache(maxsize=1)
def doc_text() -> str:
    if not RULES_DOC.exists():
        raise FileNotFoundError(f"챗봇 규칙 문서가 없습니다: {RULES_DOC}")
    return RULES_DOC.read_text(encoding="utf-8")


def _block(pattern: str, what: str) -> str:
    text = doc_text()
    m = re.search(pattern, text, flags=re.M)
    if not m:
        raise KeyError(f"규칙 문서에 {what}이(가) 없습니다.")
    rest = text[m.start():]
    nxt = re.search(r"^## ", rest[3:], flags=re.M)
    return (rest if nxt is None else rest[: nxt.start() + 3]).replace("\n---\n", "\n").strip()


def section(n: int) -> str:
    """'## n. 제목' 부터 다음 '## ' 앞까지."""
    return _block(rf"^## {n}\. .*?$", f"{n}장")


def appendix(letter: str) -> str:
    return _block(rf"^## 부록 {letter}\. .*?$", f"부록 {letter}")


def version() -> str:
    m = re.search(r"버전 ([\d.]+)", doc_text())
    return m.group(1) if m else "?"


def rules_block(*chapters: int, appendices: str = "") -> str:
    parts = [section(n) for n in chapters] + [appendix(a) for a in appendices]
    return f"[챗봇 규칙 문서 v{version()} 발췌 — 반드시 지킨다]\n\n" + "\n\n".join(parts)
