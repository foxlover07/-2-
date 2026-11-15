"""평가 케이스가 지금 코드·규칙 문서와 맞는지, L3 판정기가 같은 뜻의 다른 표현을 통과시키는지."""

from eval.cases_check import check_all, match_condition, match_plan

BAR = {"tool": "amenity", "amenity": "bar", "metric": "nearest_m", "excludes": [30], "keeps": [31]}


def test_case_files_are_valid():
    assert check_all() == []


def test_equivalent_thresholds_pass():
    for op, value in (("<=", 30), ("<", 31)):
        cond = {"tool": "amenity", "label": "술집", "amenity": "bar", "metric": "nearest_m",
                "exclude_if": {"op": op, "value": value}}
        assert match_condition(cond, BAR) == []


def test_wrong_threshold_fails():
    cond = {"tool": "amenity", "label": "술집", "amenity": "bar", "metric": "nearest_m", "exclude_if": {"op": "<=", "value": 50}}
    assert match_condition(cond, BAR)


def test_category_in_and_not_in_are_the_same_meaning():
    m = {"tool": "listing_field", "field": "property_type", "excludes": ["원룸"], "keeps": ["오피스텔"]}
    for op, value in (("in", ["원룸"]), ("not_in", ["오피스텔"])):
        cond = {"tool": "listing_field", "label": "유형", "field": "property_type", "exclude_if": {"op": op, "value": value}}
        assert match_condition(cond, m) == []


def test_plan_extra_condition_and_onsite():
    plan = {"conditions": [{"tool": "amenity", "label": "술집", "amenity": "bar", "metric": "nearest_m",
                            "exclude_if": {"op": "<=", "value": 30}},
                           {"tool": "building_field", "label": "연식", "field": "building_age_years",
                            "exclude_if": {"op": ">", "value": 20}}],
            "onsite_checks": []}
    why = match_plan(plan, {"conditions": [BAR], "exact": True, "onsite": ["방음"]})
    assert any("기대하지 않은 조건" in w for w in why)
    assert any("현장 확인 빠짐" in w for w in why)


def test_no_conditions_case():
    assert match_plan({"conditions": [], "onsite_checks": ["방음"]}, {"no_conditions": True, "onsite": ["방음"]}) == []
    assert match_plan({"conditions": [{"tool": "amenity"}]}, {"no_conditions": True})
