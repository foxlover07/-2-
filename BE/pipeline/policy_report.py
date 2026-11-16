"""정책 주거 형태 분류 보고 (app.policy.classify_housing). API 호출 없음.

사용 (BE/ 에서):
    ../home/Scripts/python.exe -X utf8 -m pipeline.policy_report      # 지금 app.policy 가 읽는 정책 파일 기준

보는 법: by_type 은 유형별 개수, unclassified 는 어느 유형에도 안 걸린 정책 (화면에는 '일반 주거 지원'으로만 나옴),
multi 는 여러 유형에 걸린 정책 (근거가 맞는지 몇 건만 확인). 틀린 분류가 보이면 HOUSING_TYPE_RULES 낱말을 고친다.
collect_policies 를 실행하면 같은 보고가 policies_<ts>.report.json 의 classification 에 들어간다.
"""

from __future__ import annotations

import json
from collections import Counter

from app.policy import PolicyItem, classify_housing, load_policies, policy_source


def classification_report(items: list[dict] | tuple[PolicyItem, ...], sample: int = 15) -> dict:
    its = [classify_housing(x if isinstance(x, PolicyItem) else PolicyItem.model_validate(x)) for x in items]
    by_type = Counter(t for it in its for t in it.housing_types)
    unclassified = [it for it in its if not it.housing_types]
    multi = [it for it in its if len(it.housing_types) >= 2]
    return {
        "total": len(its),
        "by_type": dict(by_type.most_common()),
        "unclassified_count": len(unclassified),
        "unclassified": [{"name": it.name, "subcategory": it.subcategory, "keywords": it.keywords[:5]}
                         for it in unclassified[:sample]],
        "multi_count": len(multi),
        "multi": [{"name": it.name, "types": it.housing_types, "evidence": it.housing_evidence} for it in multi[:sample]],
        "by_region": dict(Counter(it.region or "(표기 없음)" for it in its).most_common(10)),
    }


def main() -> None:
    items = load_policies()
    print(json.dumps({"source": policy_source(), **classification_report(items)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
