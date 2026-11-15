"""챗봇 규칙 문서(docs/chatbot-rules.md)를 읽어 프롬프트에 넣는다.

- 규칙 문서가 하나의 원본이다. 문서를 고치면 프롬프트도 같이 바뀐다 (rules-eval-plan.md 2장).
- 장(## n.)을 번호로 찾아 그대로 넣는다. 프롬프트마다 필요한 장만 넣어 길이를 줄인다.
  · 조건 추출: 1·2·3·6·7장  · 제외 계획: 5장  · 설명: 4·8·9장
- 고를 수 있는 건물(R-101 표)도 여기서 읽는다. 챗봇·평가 케이스 검사가 같은 목록을 쓴다.
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


def section(n: int) -> str:
    """'## n. 제목' 부터 다음 '## ' 앞까지."""
    text = doc_text()
    m = re.search(rf"^## {n}\. .*?$", text, flags=re.M)
    if not m:
        raise KeyError(f"규칙 문서에 {n}장이 없습니다.")
    rest = text[m.start():]
    nxt = re.search(r"^## ", rest[3:], flags=re.M)
    body = rest if nxt is None else rest[: nxt.start() + 3]
    return body.replace("\n---\n", "\n").strip()


def appendix(letter: str) -> str:
    text = doc_text()
    m = re.search(rf"^## 부록 {letter}\. .*?$", text, flags=re.M)
    if not m:
        raise KeyError(f"규칙 문서에 부록 {letter}가 없습니다.")
    rest = text[m.start():]
    nxt = re.search(r"^## ", rest[3:], flags=re.M)
    return (rest if nxt is None else rest[: nxt.start() + 3]).strip()


def version() -> str:
    m = re.search(r"버전 ([\d.]+)", doc_text())
    return m.group(1) if m else "?"


@lru_cache(maxsize=1)
def buildings_by_university() -> dict[str, tuple[str, ...]]:
    """R-101 표: 대학 → 고를 수 있는 건물."""
    out: dict[str, tuple[str, ...]] = {}
    for line in doc_text().splitlines():
        m = re.match(r"\| (\S+대학교) \| (.+) \|$", line)
        if m:
            out[m.group(1)] = tuple(b.strip() for b in m.group(2).split(","))
    return out


def universities() -> list[str]:
    return list(buildings_by_university())


def rules_block(*chapters: int, appendices: str = "") -> str:
    parts = [section(n) for n in chapters] + [appendix(a) for a in appendices]
    return f"[챗봇 규칙 문서 v{version()} 발췌 — 반드시 지킨다]\n\n" + "\n\n".join(parts)
