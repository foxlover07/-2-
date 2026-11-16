import httpx
import pytest

from app.tools.kakao import KakaoClient, LatLng, single_exact_match


def _client(handler):
    return KakaoClient("test-key", transport=httpx.MockTransport(handler))


def test_walk_ok_reads_distance_and_time_and_sends_lng_as_x():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(dict(req.url.params))
        seen["auth"] = req.headers["Authorization"]
        return httpx.Response(200, json={"status": "OK", "route": {"properties": {"totalDistance": 812, "totalTime": 640}}})

    r = _client(handler).walk(LatLng(lat=37.56, lng=126.94), LatLng(lat=37.57, lng=126.95))
    assert r.ok and r.distance_m == 812 and r.time_s == 640
    assert seen["start_x"] == "126.94" and seen["start_y"] == "37.56"
    assert seen["route_mode"] == "BROAD_FIRST"
    assert seen["auth"] == "KakaoAK test-key"


@pytest.mark.parametrize("status", ["START_LINK_NOT_FOUND", "TOO_FAR_AWAY", "SAME_POINT"])
def test_walk_non_ok_has_no_time(status):
    r = _client(lambda req: httpx.Response(200, json={"status": status})).walk(LatLng(0, 0), LatLng(1, 1))
    assert not r.ok and r.status == status
    assert r.time_s is None and r.distance_m is None


def test_walk_http_error_is_not_zero():
    r = _client(lambda req: httpx.Response(429, json={"msg": "quota"})).walk(LatLng(0, 0), LatLng(1, 1))
    assert r.status == "HTTP_429" and r.time_s is None


def test_invalid_route_mode():
    with pytest.raises(ValueError):
        _client(lambda req: httpx.Response(200)).walk(LatLng(0, 0), LatLng(1, 1), route_mode="FASTEST")


def test_single_exact_match_only_when_one():
    one = {"documents": [{"x": "126.9", "y": "37.5", "address_name": "a"}]}
    assert single_exact_match(one)[0] == LatLng(lat=37.5, lng=126.9)
    assert single_exact_match({"documents": []}) is None
    assert single_exact_match({"documents": [one["documents"][0]] * 2}) is None


def test_multiple_docs_resolved_only_by_exact_name():
    res = {"documents": [
        {"x": "126.947", "y": "37.567", "address_name": "서울 서대문구 신촌동 2-97"},
        {"x": "126.940", "y": "37.559", "address_name": "서울 서대문구 창천동 2-97"},
    ]}
    pos, doc = single_exact_match(res, "서울시 서대문구 신촌동 2-97")
    assert doc["address_name"] == "서울 서대문구 신촌동 2-97"
    assert single_exact_match(res, "서울시 서대문구 봉원동 2-97") is None
    assert single_exact_match(res) is None


def test_missing_key_rejected():
    with pytest.raises(ValueError):
        KakaoClient("")
