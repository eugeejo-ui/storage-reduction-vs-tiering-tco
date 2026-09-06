"""
sensitivity.py — 민감도·몬테카를로 모듈 (Phase 6)

역할
    Phase 5 가 산출한 임계 배수(90일 기준 11.30 등)는 하나의 시나리오에서
    나온 점 추정치다. 이 모듈은 그 값을 얼마나 믿을 수 있는지를 산출한다.

두 층으로 나눈다 (Phase 6 판단 ⑪)
    임계 배수에 영향을 주는 파라미터와 총비용에만 영향을 주는 파라미터가
    완전히 갈린다. 두 목록을 하나로 합치면 결론의 신뢰도를 흐린다.

        임계 배수 민감도 : 색인 DRR, 복제 계수      → 결론의 신뢰도
        총비용 민감도    : 상각기간, 프리미엄, 기준선, 로그량 → 예산 수립

    상각기간은 총비용을 정확히 2배 바꾸지만 임계 배수는 소수 셋째 자리까지
    동일하다. 비교 대상 양쪽에 똑같이 적용되어 비율이 유지되기 때문이다.

자동 추출만으로는 부족하다
    원장 로더의 sensitivity_targets() 는 assumed / unverified 등급만 잡는다.
    현재 1건(vendor_baseline)이며, 정작 결론을 움직이는 색인 DRR 과 복제 계수는
    잡히지 않는다.

    선행 프로젝트에서 상태가 격상되며 민감도 목록에서 조용히 누락된 사례가
    있었다. 따라서 자동 추출 목록과 명시적 유지 목록을 함께 운용한다.

분포 형태 (Phase 6 판단 ⑫)
    색인 DRR 은 균등분포로 뽑는다. base 값 2.00 이 근거 없이 정해졌기
    때문이다. Phase 1 에서 "계산을 진행하려면 기준값이 필요하므로 범위
    하한 쪽에 임의 설정"이라고 기록했다. 삼각분포로 처리하면 근거 없는 값에
    가중치를 주는 셈이 된다.

    복제 계수는 이산 균등분포로 뽑는다. 1/1, 3/2, 3/3 같은 실제 구성만
    존재하며 연속값이 아니기 때문이다.

예측이 아니다
    몬테카를로는 미래를 맞히려는 시도가 아니라 이미 확보된 범위 안에서의
    추출이다. 작업원칙 14항(예측하지 않는다)과 충돌하지 않는다.
"""

import random
import statistics
from dataclasses import dataclass, field

import breakeven as bk
import cost_model as cm
import overhead as ov
import tco_engine as te


class SensitivityError(Exception):
    """민감도 분석 조건이 갖춰지지 않았을 때."""


# =============================================================================
# 명시적 유지 목록 — 자동 추출에 잡히지 않지만 반드시 포함해야 하는 항목
# =============================================================================

# 임계 배수를 움직이는 항목. 결론의 신뢰도를 결정한다.
CRITICAL_RATIO_TARGETS = ("drr_tsidx", "replication")

# 총비용만 움직이는 항목. 예산 수립에 쓰인다.
TOTAL_COST_TARGETS = ("amortization_years", "oem_premium",
                      "archive_baseline", "daily_gb")

# 색인 감축률 범위 (docs/phase01_reduction.md 5절)
DRR_TSIDX_RANGE = (1.0, 6.0)

# 복제 구성 후보. Splunk 클러스터의 실제 구성만 둔다.
REPLICATION_CHOICES = (
    (1, 1, 1),   # 단일 인덱서 — 본 프로젝트의 기본이자 가장 보수적인 전제
    (3, 2, 1),   # 클러스터 기본
    (3, 2, 2),   # 클러스터 + 아카이브 2벌
    (3, 3, 1),   # 고가용 클러스터
)

# 상각기간 후보 (법인세법 시행규칙 별표 5)
AMORTIZATION_CHOICES = (4, 5, 6)


def declared_targets() -> dict:
    """명시적 유지 목록을 돌려준다.

    자동 추출 결과와 합쳐 쓰되, 이 목록이 비면 안 된다.
    검증에서 상각기간과 색인 DRR 이 빠지지 않았는지 확인한다.
    """
    return {
        "critical_ratio": list(CRITICAL_RATIO_TARGETS),
        "total_cost": list(TOTAL_COST_TARGETS),
    }


# =============================================================================
# 1. 일변량 민감도
# =============================================================================

@dataclass
class OneWayResult:
    """파라미터 하나를 흔들었을 때의 결과 변동."""
    name: str
    values: list
    outputs: list
    low: float = field(init=False)
    high: float = field(init=False)
    span: float = field(init=False)
    affects: bool = field(init=False)

    def __post_init__(self):
        vals = [v for v in self.outputs if v is not None]
        if not vals:
            raise SensitivityError(f"[{self.name}] 유효한 결과가 없습니다.")
        self.low, self.high = min(vals), max(vals)
        self.span = self.high - self.low
        # 상대 변동이 0.1% 미만이면 영향이 없다고 본다.
        self.affects = (self.span / self.low) > 1e-3 if self.low else False


def one_way_critical_ratio(base: cm.Scenario, reference_days: int = 90) -> list:
    """임계 배수에 대한 일변량 민감도."""
    out = []

    # 색인 감축률
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ys = [_crit(base, reference_days, drr_tsidx=x) for x in xs]
    out.append(OneWayResult("색인 DRR", xs, ys))

    # 복제 구성
    xs = list(REPLICATION_CHOICES)
    ys = [_crit(base, reference_days, rf=a, sf=b, archive_copies=c)
          for a, b, c in xs]
    out.append(OneWayResult("복제 구성 (RF/SF/사본)", xs, ys))

    # 영향이 없음을 확인하기 위해 함께 돌린다.
    # 이 항목들이 '영향 있음'으로 나오면 계산 어딘가가 잘못된 것이다.
    xs = list(AMORTIZATION_CHOICES)
    ys = [_crit(base, reference_days, amortization_years=x) for x in xs]
    out.append(OneWayResult("상각기간", xs, ys))

    xs = [2.8, 3.3, 3.9]
    ys = [_crit(base, reference_days, oem_premium=x) for x in xs]
    out.append(OneWayResult("정품 프리미엄", xs, ys))

    xs = [0.5, 1.0, 3.0]
    ys = [_crit(base, reference_days,
                archive_krw_per_tb=base.archive_krw_per_tb * x) for x in xs]
    out.append(OneWayResult("아카이브 기준선 배수", xs, ys))

    xs = [10, 100, 500]
    ys = [_crit(base, reference_days, daily_gb=x) for x in xs]
    out.append(OneWayResult("일일 로그량", xs, ys))

    return out


def one_way_total_cost(base: cm.Scenario, years: int = 5) -> list:
    """총비용에 대한 일변량 민감도. 예산 수립용."""
    out = []

    xs = list(AMORTIZATION_CHOICES)
    ys = [_total(base, years, amortization_years=x) for x in xs]
    out.append(OneWayResult("상각기간", xs, ys))

    xs = [2.8, 3.3, 3.9]
    ys = [_total(base, years, oem_premium=x) for x in xs]
    out.append(OneWayResult("정품 프리미엄", xs, ys))

    xs = [1.0, 2.0, 3.0, 6.0]
    ys = [_total(base, years, drr_tsidx=x) for x in xs]
    out.append(OneWayResult("색인 DRR", xs, ys))

    xs = list(REPLICATION_CHOICES)
    ys = [_total(base, years, rf=a, sf=b, archive_copies=c) for a, b, c in xs]
    out.append(OneWayResult("복제 구성 (RF/SF/사본)", xs, ys))

    xs = [0.5, 1.0, 3.0]
    ys = [_total(base, years,
                 archive_krw_per_tb=base.archive_krw_per_tb * x) for x in xs]
    out.append(OneWayResult("아카이브 기준선 배수", xs, ys))

    return out


# =============================================================================
# 2. 몬테카를로 — 임계 배수의 신뢰구간
# =============================================================================

@dataclass
class MonteCarloResult:
    """임계 배수의 분포."""
    reference_days: int
    samples: list
    deterministic: float
    n: int = field(init=False)
    median: float = field(init=False)
    p05: float = field(init=False)
    p25: float = field(init=False)
    p75: float = field(init=False)
    p95: float = field(init=False)

    def __post_init__(self):
        s = sorted(x for x in self.samples if x is not None)
        if not s:
            raise SensitivityError("유효한 표본이 없습니다.")
        self.n = len(s)
        self.median = statistics.median(s)
        self.p05, self.p25, self.p75, self.p95 = (
            _pct(s, 5), _pct(s, 25), _pct(s, 75), _pct(s, 95)
        )

    def prob_below(self, r: float) -> float:
        """임계값이 주어진 배수보다 낮을 확률.

        즉 '현재 시장 배수에서 해당 구성을 수용할 수 있을 확률'이다.
        """
        s = [x for x in self.samples if x is not None]
        return sum(1 for x in s if x < r) / len(s)


def monte_carlo_critical_ratio(
    base: cm.Scenario,
    reference_days: int = 90,
    iterations: int = 2000,
    seed: int = 20260905,
    fix_replication: bool = False,
) -> MonteCarloResult:
    """임계 배수의 분포를 추출한다.

    Parameters
    ----------
    fix_replication : bool
        True 면 복제 구성을 기본값(1/1/1)에 고정하고 색인 DRR 만 흔든다.
        복제 구성은 조직이 이미 정해둔 값이므로, 그것이 확정된 경우의
        불확실성을 따로 보기 위한 선택지다.

    Notes
    -----
    색인 DRR 은 균등분포로 뽑는다. base 값에 근거가 없기 때문이다.
    복제 구성은 실제 구성 목록에서 이산 균등으로 뽑는다.
    """
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        drr = rng.uniform(*DRR_TSIDX_RANGE)
        if fix_replication:
            rf, sf, ac = 1, 1, 1
        else:
            rf, sf, ac = rng.choice(REPLICATION_CHOICES)
        samples.append(_crit(base, reference_days,
                             drr_tsidx=drr, rf=rf, sf=sf, archive_copies=ac))

    det = _crit(base, reference_days)
    return MonteCarloResult(reference_days=reference_days,
                            samples=samples, deterministic=det)


# =============================================================================
# 3. 판정 뒤집힘 확률
# =============================================================================

@dataclass
class VerdictStability:
    """현재 시장 배수에서의 판정과 그 안정성."""
    reference_days: int
    market_ratio: float
    deterministic_critical: float
    deterministic_verdict: str      # '수용 가능' | '수용 불가'
    prob_acceptable: float
    stable: bool = field(init=False)

    def __post_init__(self):
        # 확률이 한쪽으로 90% 이상 쏠려야 안정적이라고 본다.
        self.stable = (self.prob_acceptable >= 0.9
                       or self.prob_acceptable <= 0.1)


def verdict_stability(
    base: cm.Scenario,
    reference_days: int,
    market_ratio: float = 22.0,
    iterations: int = 2000,
    seed: int = 20260905,
    fix_replication: bool = False,
) -> VerdictStability:
    """Phase 5 의 판정이 얼마나 견고한지 확률로 다시 쓴다."""
    mc = monte_carlo_critical_ratio(base, reference_days, iterations,
                                    seed, fix_replication)
    det = mc.deterministic
    return VerdictStability(
        reference_days=reference_days,
        market_ratio=market_ratio,
        deterministic_critical=det,
        deterministic_verdict="수용 가능" if det < market_ratio else "수용 불가",
        prob_acceptable=mc.prob_below(market_ratio),
    )


# =============================================================================
# 4. CSV 저장
# =============================================================================

def write_sensitivity_csv(results, path, value_label="value"):
    import csv
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["parameter", "input", value_label, "affects_output"])
        for r in results:
            for x, y in zip(r.values, r.outputs):
                w.writerow([r.name, str(x),
                            "" if y is None else round(y, 6), r.affects])
    return path


def write_monte_carlo_csv(results, path):
    import csv
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["reference_days", "n", "deterministic",
                    "p05", "p25", "median", "p75", "p95"])
        for r in results:
            w.writerow([r.reference_days, r.n, round(r.deterministic, 4),
                        round(r.p05, 4), round(r.p25, 4), round(r.median, 4),
                        round(r.p75, 4), round(r.p95, 4)])
    return path


# =============================================================================
# 내부 도우미
# =============================================================================

def _pct(sorted_values, p):
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def _crit(base: cm.Scenario, reference_days: int, **overrides):
    sc = cm.Scenario(**{**base.__dict__, **overrides})
    return bk.critical_ratio(sc, reference_days=reference_days).critical_r


def _total(base: cm.Scenario, years: int, **overrides):
    fields = {**base.__dict__, **overrides}
    fields.pop("years", None)
    sc = cm.Scenario(**fields, years=years)
    return te.compute_tco(sc, terms=(years,)).term(years).onprem_krw


if __name__ == "__main__":
    from pricing_loader import load

    ls = load()
    base = cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90, years=5,
        archive_krw_per_tb=cm.archive_baseline(ls.media),
        oem_premium=ls.media.get_float("array_oem_premium", "base"),
    )

    print("[sensitivity.py] Phase 6 산출")
    print()

    print("### 자동 추출 목록")
    auto = ls.media.sensitivity_targets() + ls.cloud.sensitivity_targets()
    print(f"  {[k for k, _ in auto] or '없음'}")
    print("### 명시적 유지 목록")
    d = declared_targets()
    print(f"  임계 배수: {d['critical_ratio']}")
    print(f"  총비용   : {d['total_cost']}")
    print()

    print("### 1. 일변량 민감도 — 임계 배수 (90일 기준)")
    print("| 파라미터 | 최소 | 최대 | 변동 폭 | 영향 |")
    print("|---|---|---|---|---|")
    for r in one_way_critical_ratio(base):
        mark = "**있음**" if r.affects else "없음"
        print(f"| {r.name} | {r.low:.3f} | {r.high:.3f} | {r.span:.3f} | {mark} |")
    print()

    print("### 2. 일변량 민감도 — 총비용 (5년)")
    print("| 파라미터 | 최소 | 최대 | 배수 |")
    print("|---|---|---|---|")
    for r in one_way_total_cost(base):
        print(f"| {r.name} | {r.low/1e6:,.2f}백만 | {r.high/1e6:,.2f}백만 | "
              f"{r.high/r.low:.2f}배 |")
    print()

    print("### 3. 임계 배수 신뢰구간 (복제 구성 포함)")
    print("| 기준 | 결정론 | p05 | 중앙값 | p95 |")
    print("|---|---|---|---|---|")
    for ref in (30, 60, 90, 180, 365):
        mc = monte_carlo_critical_ratio(base, ref, iterations=1000)
        print(f"| {ref}일 | {mc.deterministic:.2f} | {mc.p05:.2f} | "
              f"{mc.median:.2f} | {mc.p95:.2f} |")
    print()

    print("### 4. 판정 안정성 (현재 시장 R=22)")
    print("| 기준 | 결정론 판정 | 수용 가능 확률 | 안정 |")
    print("|---|---|---|---|")
    for ref in (30, 60, 90, 180, 365):
        v = verdict_stability(base, ref, iterations=1000)
        print(f"| {ref}일 | {v.deterministic_verdict} | "
              f"{v.prob_acceptable*100:.1f}% | {'예' if v.stable else '**아니오**'} |")
