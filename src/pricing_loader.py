"""
pricing_loader.py — 가격 원장 로더 (Phase 4)

역할
    data/media_pricing.yaml 과 data/cloud_pricing.yaml 을 읽어
    계산 코드가 쓸 수 있는 형태로 제공한다.
    코드에 숫자를 직접 적지 않기 위한 계층이며, 값이 바뀌면 yaml 만 고치면 된다.

선행 프로젝트에서 승계한 원칙
    1. 값을 꺼낼 때 status 를 함께 확인한다.
       pending 항목을 모르고 쓰면 None 이 계산에 섞여 조용히 틀린 결과가 나온다.
       그래서 값이 없으면 명시적으로 예외를 던진다.
    2. low/base/high 중 어느 것을 쓸지 호출자가 명시한다.
    3. USD 항목은 환율을 곱해 KRW 로 환산한다.

본 프로젝트에서 신설한 원칙 — 단위계 분리 (리스크 R7)
    Phase 0 4.2절에서 두 경로를 합산하지 않기로 확정했다. 그러나 문서에만
    적어두면 코드에서 언제든 위반할 수 있다. 따라서 값을 실수(float)가 아니라
    출처 원장과 단위를 함께 지닌 Quantity 로 반환하고, 서로 다른 원장의 값을
    더하려 하면 예외를 던진다.

    선행 원장의 ssd_price / hdd_price / object_price 는 이름이 매체명과 유사하나
    매체 구매 원가가 아니라 클라우드 서비스 요금이다. 이름만 보고 전용하면
    단위(월 이용료 대 일시 구매가), 연동 시차, 비용 성격(운영비 대 자본적 지출)이
    모두 어긋난다. 이 실수를 사람의 주의력이 아니라 구조로 막는다.

스키마 차이
    선행 프로젝트의 원장은 2단계 중첩이었고 각 항목에 name 필드가 있었다.
    본 프로젝트의 원장은 3단계까지 중첩되며 name 필드가 없다. 따라서 재귀
    탐색으로 status 필드를 가진 노드를 항목으로 인식한다.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml


# 계산에 사용해도 되는 상태
#   confirmed  : 1차 출처로 검증됨
#   partial    : 일부만 확인됨
#   unverified : 값은 있으나 출처 미확보
#   assumed    : 근거 없는 범위 가정 (민감도 분석 필수)
USABLE_STATUS = {"confirmed", "partial", "unverified", "assumed", "derived"}
# 계산에 사용하면 안 되는 상태
BLOCKED_STATUS = {"pending", "blank", "deprecated"}
# 근거가 약해 민감도 분석이 반드시 필요한 상태
NEEDS_SENSITIVITY = {"assumed", "unverified"}

# 원장 종류
LEDGER_MEDIA = "media_capex"
LEDGER_CLOUD = "cloud_opex"


class PricingError(Exception):
    """원장에서 값을 꺼낼 수 없을 때."""


class UnitSystemError(PricingError):
    """단위계가 다른 값을 섞으려 할 때.

    Phase 0 4.2절의 합산 금지 원칙을 코드 수준에서 강제한다.
    """


@dataclass(frozen=True)
class Quantity:
    """값과 단위, 출처 원장을 함께 지닌 수치.

    실수(float) 대신 이 객체를 반환하는 이유는 단순하다. 실수로 반환하면
    단위 정보가 사라져 서로 다른 원장의 값을 더해도 아무 일도 일어나지 않는다.
    """
    amount: float
    unit: str
    ledger_type: str
    key: str = ""

    def _check(self, other):
        if not isinstance(other, Quantity):
            raise UnitSystemError(
                f"Quantity 끼리만 연산할 수 있습니다. 받은 타입: {type(other).__name__}"
            )
        if self.ledger_type != other.ledger_type:
            raise UnitSystemError(
                f"단위계가 다른 값을 연산할 수 없습니다.\n"
                f"  {self.key or '(무명)'}: {self.ledger_type} ({self.unit})\n"
                f"  {other.key or '(무명)'}: {other.ledger_type} ({other.unit})\n"
                f"온프레미스 매체 구매 원가와 클라우드 서비스 이용료는 "
                f"단위와 비용 성격이 다르므로 합산하거나 평균할 수 없습니다. "
                f"(phase00_scope.md 4.2절)"
            )
        if self.unit != other.unit:
            raise UnitSystemError(
                f"단위가 다른 값을 연산할 수 없습니다: "
                f"{self.unit!r} vs {other.unit!r}"
            )

    def __add__(self, other):
        self._check(other)
        return Quantity(self.amount + other.amount, self.unit,
                        self.ledger_type, f"{self.key}+{other.key}")

    def __sub__(self, other):
        self._check(other)
        return Quantity(self.amount - other.amount, self.unit,
                        self.ledger_type, f"{self.key}-{other.key}")

    def __mul__(self, scalar):
        """스칼라 배만 허용한다. Quantity 끼리 곱하면 단위가 바뀐다."""
        if isinstance(scalar, Quantity):
            raise UnitSystemError(
                "Quantity 끼리 곱할 수 없습니다. 단위가 바뀌므로 "
                "호출부에서 결과 단위를 명시해 새 Quantity 를 만드십시오."
            )
        return Quantity(self.amount * scalar, self.unit, self.ledger_type, self.key)

    __rmul__ = __mul__

    def __float__(self):
        """부득이 실수가 필요한 경우를 위한 탈출구.

        의도적으로 명시적 변환만 허용한다. 암묵적 변환은 지원하지 않는다.
        """
        return float(self.amount)

    def __repr__(self):
        return f"Quantity({self.amount!r}, {self.unit!r}, {self.ledger_type!r})"


@dataclass
class PriceItem:
    """원장 한 항목."""
    key: str
    path: str
    low: float
    base: float
    high: float
    unit: str
    status: str
    confidence: str
    source_url: str
    note: str
    ledger_type: str
    adopted: bool = True
    rejection_reason: str = ""

    def raw(self, which="base"):
        v = getattr(self, which, None)
        if v is None:
            raise PricingError(
                f"[{self.key}] '{which}' 값이 비어 있습니다 (status={self.status}). "
                f"원장을 채우거나 다른 which 를 지정하십시오."
            )
        return v

    @property
    def is_usd(self):
        return "USD" in (self.unit or "")


class Ledger:
    """가격 원장 하나."""

    def __init__(self, path, fx_rate=None):
        self.path = Path(path)
        with open(self.path, encoding="utf-8") as f:
            self._raw = yaml.safe_load(f)

        meta = self._raw.get("meta", {}) or {}
        self.ledger_type = meta.get("ledger_type")
        self.unit_system = meta.get("unit_system")
        self.fx_rate = fx_rate if fx_rate is not None else meta.get("fx_rate")
        self.fx_basis = meta.get("fx_basis", "")

        if not self.ledger_type:
            raise PricingError(f"{self.path.name}: meta.ledger_type 이 없습니다.")
        if not self.unit_system:
            raise PricingError(f"{self.path.name}: meta.unit_system 이 없습니다.")

        self._items = {}
        self._index(self._raw, "")

    def _index(self, node, path):
        """재귀 탐색으로 status 필드를 가진 노드를 항목으로 등록한다."""
        if not isinstance(node, dict):
            return
        if "status" in node:
            key = path.split(".")[-1]
            if key in self._items:
                raise PricingError(f"중복 항목 키: {key} ({path})")
            self._items[key] = PriceItem(
                key=key,
                path=path,
                low=node.get("low"),
                base=node.get("base"),
                high=node.get("high"),
                unit=node.get("unit", ""),
                status=node.get("status", "unknown"),
                confidence=node.get("confidence", ""),
                source_url=node.get("source_url", ""),
                note=node.get("note", "") or node.get("reason", ""),
                ledger_type=self.ledger_type,
                adopted=node.get("adopted", True),
                rejection_reason=node.get("rejection_reason", ""),
            )
            return
        for k, v in node.items():
            if k == "meta":
                continue
            self._index(v, f"{path}.{k}" if path else k)

    # --- 조회 ---------------------------------------------------------------

    def item(self, key):
        if key not in self._items:
            raise PricingError(
                f"{self.path.name} 에 '{key}' 항목이 없습니다. "
                f"다른 원장의 항목을 찾고 있는 것은 아닌지 확인하십시오."
            )
        return self._items[key]

    def get(self, key, which="base") -> Quantity:
        """값을 Quantity 로 꺼낸다. 사용 불가 상태면 예외를 던진다."""
        it = self.item(key)
        if it.status in BLOCKED_STATUS:
            raise PricingError(
                f"[{key}] status='{it.status}' 이므로 계산에 사용할 수 없습니다. "
                f"원장을 먼저 채우십시오. 사유: {it.note[:80]}"
            )
        if not it.adopted:
            raise PricingError(
                f"[{key}] 는 검증 결과 미채택된 항목이므로 계산에 사용할 수 없습니다.\n"
                f"  사유: {it.rejection_reason.strip()[:160]}\n"
                f"  원장에 adopted: false 로 기록되어 있습니다. 값을 참조만 하려면 "
                f"item('{key}') 로 직접 접근하십시오."
            )
        return Quantity(it.raw(which), it.unit, it.ledger_type, key)

    def get_krw(self, key, which="base") -> Quantity:
        """USD 항목이면 환율을 곱해 KRW 로 환산해 반환한다."""
        it = self.item(key)
        q = self.get(key, which)
        if not it.is_usd:
            return q
        if self.fx_rate is None:
            raise PricingError(
                f"[{key}] USD 항목인데 환율이 없습니다. meta.fx_rate 를 채우십시오."
            )
        return Quantity(q.amount * self.fx_rate,
                        it.unit.replace("USD", "KRW"),
                        it.ledger_type, key)

    def get_float(self, key, which="base") -> float:
        """단위 없는 배수·연수 항목용. 단위가 붙은 항목에는 쓰지 않는다."""
        it = self.item(key)
        if it.unit not in ("", "multiple", "years", None):
            raise UnitSystemError(
                f"[{key}] 는 단위({it.unit})가 있는 항목이므로 get() 을 쓰십시오. "
                f"get_float() 은 배수·연수 등 무차원 항목 전용입니다."
            )
        return float(self.get(key, which).amount)

    # --- 진단 ---------------------------------------------------------------

    def keys(self):
        return sorted(self._items.keys())

    def status_summary(self):
        from collections import Counter
        return dict(Counter(i.status for i in self._items.values()))

    def blocked_items(self):
        return [(k, i.status) for k, i in self._items.items()
                if i.status in BLOCKED_STATUS]

    def missing_source(self):
        return [k for k, i in self._items.items()
                if i.status in ("confirmed", "partial") and not i.source_url]

    def rejected_items(self):
        """검증 결과 미채택된 항목. 참조는 가능하나 계산에는 쓸 수 없다."""
        return [(k, i.rejection_reason.strip()[:60])
                for k, i in self._items.items() if not i.adopted]

    def sensitivity_targets(self):
        """근거가 약해 민감도 분석이 반드시 필요한 항목.

        선행 프로젝트에서 상태가 격상되면서 이 목록에서 조용히 누락된 사례가
        있었으므로, 호출부는 이 목록에 더해 명시적 유지 목록도 함께 운용한다.
        """
        return [(k, i.status) for k, i in self._items.items()
                if i.status in NEEDS_SENSITIVITY and i.adopted]

    def items_without_base(self):
        """base 가 비어 있는 항목.

        ratio.scan_range 처럼 대표값을 두지 않는 것이 설계 의도인 항목이
        여기에 잡힌다. 이 목록이 비면 오히려 설계가 훼손된 것이다.
        """
        return [k for k, i in self._items.items() if i.base is None]


class LedgerSet:
    """두 원장을 함께 다루되 섞이지 않게 관리한다."""

    def __init__(self, media: Ledger, cloud: Ledger):
        if media.ledger_type != LEDGER_MEDIA:
            raise PricingError(
                f"매체 원장의 ledger_type 이 '{LEDGER_MEDIA}' 가 아닙니다: "
                f"{media.ledger_type}"
            )
        if cloud.ledger_type != LEDGER_CLOUD:
            raise PricingError(
                f"클라우드 원장의 ledger_type 이 '{LEDGER_CLOUD}' 가 아닙니다: "
                f"{cloud.ledger_type}"
            )
        if media.unit_system == cloud.unit_system:
            raise PricingError(
                "두 원장의 unit_system 이 같습니다. 분리 설계가 훼손되었습니다."
            )
        self.media = media
        self.cloud = cloud

    def overlapping_keys(self):
        """양쪽 원장에 같은 이름으로 존재하는 항목.

        이름이 같으면 혼동이 발생하므로 비어 있어야 한다.
        """
        return sorted(set(self.media.keys()) & set(self.cloud.keys()))


def load(data_dir=None) -> LedgerSet:
    """기본 경로에서 두 원장을 읽는다."""
    if data_dir is None:
        here = Path(__file__).resolve().parent
        data_dir = here.parent / "data"
    data_dir = Path(data_dir)
    media = Ledger(data_dir / "media_pricing.yaml")
    cloud = Ledger(data_dir / "cloud_pricing.yaml")
    return LedgerSet(media, cloud)


if __name__ == "__main__":
    ls = load()
    for name, led in (("매체(온프레미스)", ls.media), ("클라우드(대조)", ls.cloud)):
        print(f"[{name}] {led.path.name}")
        print(f"  ledger_type : {led.ledger_type}")
        print(f"  unit_system : {led.unit_system}")
        print(f"  항목 수      : {len(led.keys())}")
        print(f"  상태 집계    : {led.status_summary()}")
        blocked = led.blocked_items()
        if blocked:
            print(f"  계산 불가    : {[k for k, _ in blocked]}")
        nb = led.items_without_base()
        if nb:
            print(f"  base 미설정  : {nb}  ← 대표값을 두지 않는 것이 설계 의도")
        rj = led.rejected_items()
        if rj:
            print(f"  미채택       : {[k for k, _ in rj]}  ← 계산 사용 시 예외")
        ms = led.missing_source()
        print(f"  출처 누락    : {ms if ms else '없음'}")
        st = led.sensitivity_targets()
        print(f"  민감도 대상  : {[k for k, _ in st] if st else '없음'}")
        print()

    print(f"양쪽 원장 중복 키: {ls.overlapping_keys() or '없음'}")
    print()

    print("[단위계 분리 동작 확인]")
    m = ls.media.get_krw("hdd_manufacturer_realized")
    c = ls.cloud.get("object_price")
    print(f"  매체     : {m}")
    print(f"  클라우드 : {c}")
    try:
        _ = m + c
        print("  ✗ 합산이 통과했습니다. 분리 설계가 작동하지 않습니다.")
    except UnitSystemError as e:
        print(f"  ○ 합산 차단됨: {str(e).splitlines()[0]}")
