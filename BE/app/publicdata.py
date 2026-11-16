"""공공데이터포털(data.go.kr) 호출 공통부.

- 키: DATA_GO_KR_SERVICE_KEY (BE/.env.local). Encoding 키(% 포함)를 넣어도 Decoding 형태로 바꿔 쓴다
  (그대로 보내면 이중 인코딩으로 SERVICE_KEY_IS_NOT_REGISTERED_ERROR — 2026-11-15 확인).
- 오류 응답(OpenAPI_ServiceResponse)은 종류별 예외로 바꾼다. 키는 예외·로그에 넣지 않는다.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import httpx

from app.config import _read

TIMEOUT_S = 30


class PublicDataError(RuntimeError):
    """kind: not_registered(활용신청·승인 전) · limit(호출 한도) · no_key · http · bad_response · other"""

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


def service_key() -> str:
    key = _read("DATA_GO_KR_SERVICE_KEY")
    if not key:
        raise PublicDataError("DATA_GO_KR_SERVICE_KEY 를 BE/.env.local 에 넣어야 합니다.", "no_key")
    key = key.strip()
    return unquote(key) if "%" in key else key


_ERR = re.compile(r"<errMsg>([^<]*)</errMsg>|\"errMsg\"\s*:\s*\"([^\"]*)\"")


def check_error(text: str) -> None:
    """포털 공통 오류 응답이면 예외. 정상 응답이면 아무것도 하지 않는다."""
    m = _ERR.search(text)
    if not m:
        return
    err = m.group(1) or m.group(2) or ""
    if "NOT_REGISTERED" in err:
        raise PublicDataError(f"등록되지 않은 서비스키 ({err}) — 이 API 의 활용신청·승인을 확인하세요.", "not_registered")
    if "LIMITED" in err or "EXCEEDS" in err:
        raise PublicDataError(f"호출 한도 초과 ({err})", "limit")
    raise PublicDataError(f"공공데이터 오류 ({err})", "other")


def get(url: str, params: dict[str, str], *, client: httpx.Client | None = None) -> str:
    """GET 한 번. 정상 응답 본문(text)을 돌려준다. 재시도는 호출하는 쪽에서 정한다."""
    c = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        r = c.get(url, params={"serviceKey": service_key(), **params})
    except httpx.HTTPError as e:
        raise PublicDataError(f"연결 실패 ({type(e).__name__})", "http") from e
    finally:
        if client is None:
            c.close()
    check_error(r.text)
    if r.status_code >= 400:
        raise PublicDataError(f"HTTP {r.status_code}", "http")
    return r.text


def xml_items(text: str) -> tuple[list[dict[str, str]], int | None, str | None]:
    """국토부 실거래가 XML → (item 목록, totalCount, resultCode). 값은 앞뒤 공백을 지운 문자열 ('' = 없음)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise PublicDataError("XML 이 아닙니다", "bad_response") from e
    code = root.findtext(".//resultCode")
    if code not in (None, "000", "00"):
        raise PublicDataError(f"resultCode {code} ({root.findtext('.//resultMsg')})", "other")
    items = [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]
    total = root.findtext(".//totalCount")
    return items, int(total) if total and total.isdigit() else None, code
