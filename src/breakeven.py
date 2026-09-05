"""
breakeven.py — 손익분기 탐색 모듈 (Phase 5)

역할
    tco_engine.py 가 산출한 총비용을 축 위에서 훑어, 두 설계안의 비용이
    같아지는 지점을 찾는다.

손익분기의 정의가 Phase 5 에서 교체되었다 (판단 ⑧)
    착수 시 정의는 "감축안과 계층화안의 비용이 같아지는 배수 R" 이었다.
    Phase 4 에서 그 값이 R = 1.731 로 산출되었으나, 관측된 시장 범위
    (3.0 ~ 30.0) 밖이다. 단가가 같아도(R = 1.0) 계층화가 유리하기 때문이다.
    계층화의 이득이 단가 격차가 아니라 색인 제거에서 나오는 탓이다.

    따라서 범위 밖의 전환점을 답으로 내면 "당연히 계층화하십시오" 한 줄로
    끝난다. 대신 정의를 다음으로 교체한다.

        등비용 기간 : 구성이 바뀌었을 때, 같은 예산으로 즉시 검색 가능
                     기간을 며칠까지 유지할 수 있는가

    이 정의에서 배수 R 은 사라지지 않는다. R 이 클수록 아카이브가 상대적으로
    싸지므로 구성 변경의 대가가 작아진다. 즉 R 은 "며칠을 잃는가"를 결정하는
    변수로 역할이 바뀐다.

탐색 방식 (선행 프로젝트 승계)
    비용 차이 곡선에 계단형 요철이 있을 수 있으므로, 전 구간을 훑어 부호
    변화를 모두 찾은 뒤 각 구간 내부를 이분법으로 정밀 탐색한다.

    본 프로젝트에서 계단이 생기는 지점은 재구매 횟수다.
    ceil(계약연수 / 상각연수) 가 정수이므로 상각기간을 훑으면 비용이
    계단으로 뛴다. 이분법만 쓰면 이 구간을 놓친다.
"""

import math
from dataclasses import dataclass, field

import cost_model as cm
import overhead as ov
import tco_engine as te


class BreakevenError(Exception):
    """손익분기를 산출할 수 없을 때."""


# =============================================================================
# 공통 탐색기 — 전 구간 스캔 후 이분법
# =============================================================================

@dataclass
class Crossing:
    """부호가 바뀐 구간과 그 안에서 찾은 교차점."""
    lower: float
    upper: float
    root: float
    direction: str  # 'up' | 'down' — 차이가 음에서 양으로 바뀌면 'up'


def find_crossings(f, lo, hi, steps=200, tol=1e-6, max_iter=80):
    """f(x) 의 부호가 바뀌는 지점을 모두 찾는다.

    전 구간을 steps 등분해 훑고, 부호가 바뀐 구간마다 이분법을 돌린다.
    이분법만 쓰면 교차가 여러 번 있을 때 하나만 찾고 끝난다.

    Parameters
    ----------
    f : callable
        차이 함수. 부호가 바뀌는 지점을 찾는다.
    lo, hi : float
        탐색 범위.
    steps : int
        전 구간 스캔 분할 수. 계단을 놓치지 않을 만큼 촘촘해야 한다.

    Returns
    -------
    list[Crossing]
    """
    if lo >= hi:
        raise BreakevenError(f"탐색 범위가 잘못되었습니다: lo={lo}, hi={hi}")

    xs = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    vals = [f(x) for x in xs]

    out = []
    for i in range(steps):
        a, b = vals[i], vals[i + 1]
        if a == 0:
            out.append(Crossing(xs[i], xs[i], xs[i], "zero"))
            continue
        if a * b >= 0:
            continue
        direction = "up" if a < 0 else "down"
        left, right = xs[i], xs[i + 1]
        fa = a
        for _ in range(max_iter):
            mid = (left + right) / 2
            fm = f(mid)
            if abs(fm) < tol or (right - left) < tol:
                break
            if fa * fm < 0:
                right = mid
            else:
                left, fa = mid, fm
        out.append(Crossing(xs[i], xs[i + 1], (left + right) / 2, direction))
    return out


# =============================================================================
# 1. 배수 R 손익분기 — 기록용
# =============================================================================

@dataclass
class RatioBreakeven:
    """감축안과 계층화안의 비용이 같아지는 배수 R.

    Phase 4 에서 관측 범위 밖임이 확인되었다. 답으로 쓰지 않고 기록으로만
    남긴다. in_range 가 False 라는 사실 자체가 결과다.
    """
    site_config: str
    root: float
    in_range: bool
    scan_low: float
    scan_high: float
    note: str = ""


def ratio_breakeven(
    base_scenario: cm.Scenario,
    site_config: str,
    scan_low: float = 3.0,
    scan_high: float = 30.0,
    search_low: float = 1.0,
    search_high: float = 50.0,
) -> RatioBreakeven:
    """계층화 없음(전량 플래시)과 최대 계층화의 비용이 같아지는 R.

    탐색은 관측 범위보다 넓게 하되, 결과가 관측 범위 안인지 함께 보고한다.
    """
    def diff(r):
        no_tier = _total(base_scenario, ratio_r=r, flash_days=base_scenario.retention_days,
                         site_config=site_config)
        full_tier = _total(base_scenario, ratio_r=r, flash_days=1,
                           site_config=site_config)
        return no_tier - full_tier   # 양수면 계층화 유리

    crossings = find_crossings(diff, search_low, search_high, steps=200)
    if not crossings:
        return RatioBreakeven(
            site_config=site_config, root=None, in_range=False,
            scan_low=scan_low, scan_high=scan_high,
            note=f"탐색 범위 {search_low}~{search_high} 안에서 교차점이 없습니다. "
                 f"전 구간에서 한쪽이 우세합니다.",
        )
    root = crossings[0].root
    return RatioBreakeven(
        site_config=site_config, root=root,
        in_range=(scan_low <= root <= scan_high),
        scan_low=scan_low, scan_high=scan_high,
        note="" if scan_low <= root <= scan_high
             else f"관측 범위({scan_low}~{scan_high}) 밖입니다.",
    )


# =============================================================================
# 2. 등비용 기간 — Phase 5 의 주 산출물
# =============================================================================

@dataclass
class EquivalentDays:
    """같은 예산으로 유지할 수 있는 즉시 검색 기간."""
    ratio_r: float
    reference_days: int
    reference_cost: float
    reference_site: str
    target_site: str
    days: int
    lost_days: int = field(init=False)
    feasible: bool = field(init=False)

    def __post_init__(self):
        self.lost_days = self.reference_days - self.days
        self.feasible = self.days > 0


def equivalent_days(
    base_scenario: cm.Scenario,
    ratio_r: float,
    reference_days: int,
    reference_site: str = ov.SITE_SINGLE,
    target_site: str = ov.SITE_MULTI,
) -> EquivalentDays:
    """기준 구성의 비용과 같아지는, 대상 구성의 즉시 검색 기간을 찾는다.

    읽는 법
        단일 사이트에서 90일 설계를 하던 조직이 3개 사이트 분산을 요구받았을 때,
        같은 예산으로 즉시 검색을 며칠까지 유지할 수 있는가.

    Returns
    -------
    EquivalentDays
        days = 0 이면 대상 구성에서는 어떤 기간으로도 기준 예산을 맞출 수 없다.
    """
    target_cost = _total(base_scenario, ratio_r=ratio_r,
                         flash_days=reference_days, site_config=reference_site)

    def cost_at(d):
        return _total(base_scenario, ratio_r=ratio_r,
                      flash_days=max(1, int(round(d))), site_config=target_site)

    # 최소 기간에서도 예산을 넘으면 불가능
    if cost_at(1) > target_cost:
        return EquivalentDays(
            ratio_r=ratio_r, reference_days=reference_days,
            reference_cost=target_cost, reference_site=reference_site,
            target_site=target_site, days=0,
        )
    # 최대 기간에서도 예산 이하면 제약이 없다
    if cost_at(base_scenario.retention_days) <= target_cost:
        return EquivalentDays(
            ratio_r=ratio_r, reference_days=reference_days,
            reference_cost=target_cost, reference_site=reference_site,
            target_site=target_site, days=base_scenario.retention_days,
        )

    lo, hi = 1, base_scenario.retention_days
    for _ in range(60):
        mid = (lo + hi) / 2
        if cost_at(mid) < target_cost:
            lo = mid
        else:
            hi = mid
    return EquivalentDays(
        ratio_r=ratio_r, reference_days=reference_days,
        reference_cost=target_cost, reference_site=reference_site,
        target_site=target_site, days=int(round(lo)),
    )


# =============================================================================
# 2b. 임계 배수 — 구성 변경을 수용할 수 있는 최소 R (Phase 5 핵심 산출물)
# =============================================================================

@dataclass
class CriticalRatio:
    """기준 설계와 같은 예산으로 대상 구성을 수용할 수 있는 최소 배수 R.

    읽는 법
        즉시검색 90일을 유지하던 조직이 3사이트 분산을 요구받았을 때,
        배수 R 이 11.30 이상이어야 같은 예산 안에서 수용할 수 있다.

    역설적 성질
        기준 기간이 짧을수록 임계 R 이 높아진다. 이미 계층화를 공격적으로
        한 조직은 더 줄일 여지가 없어, 오버헤드 증가를 상쇄할 방법이 없기
        때문이다. 즉 계층화를 많이 해둔 조직일수록 가용성 요건 추가를
        감당하기 어렵다.
    """
    reference_days: int
    reference_site: str
    target_site: str
    critical_r: float
    in_range: bool
    scan_low: float
    scan_high: float


def critical_ratio(
    base_scenario: cm.Scenario,
    reference_days: int,
    reference_site: str = ov.SITE_SINGLE,
    target_site: str = ov.SITE_MULTI,
    scan_low: float = 3.0,
    scan_high: float = 30.0,
    search_low: float = 1.0,
    search_high: float = 60.0,
) -> CriticalRatio:
    """대상 구성을 기준 예산 안에서 수용할 수 있는 최소 배수 R 을 찾는다.

    비교 대상
        기준 : reference_site 에서 reference_days 로 운영
        대상 : target_site 에서 최대한 계층화(1일)했을 때
    """
    def diff(r):
        ref = _total(base_scenario, ratio_r=r, flash_days=reference_days,
                     site_config=reference_site)
        best_target = _total(base_scenario, ratio_r=r, flash_days=1,
                             site_config=target_site)
        return ref - best_target   # 양수면 수용 가능

    crossings = find_crossings(diff, search_low, search_high, steps=500)
    root = crossings[0].root if crossings else None
    return CriticalRatio(
        reference_days=reference_days,
        reference_site=reference_site,
        target_site=target_site,
        critical_r=root,
        in_range=(root is not None and scan_low <= root <= scan_high),
        scan_low=scan_low, scan_high=scan_high,
    )


# =============================================================================
# 3. 오버헤드 손익분기 — 계층화가 무의미해지는 지점
# =============================================================================

def overhead_breakeven(
    base_scenario: cm.Scenario,
    ratio_r: float,
    search_low: float = 1.0,
    search_high: float = 200.0,
) -> float:
    """아카이브 오버헤드가 얼마를 넘으면 계층화가 손해가 되는가.

    오버헤드 계수를 직접 흔들어야 하므로 overhead 모듈의 상수를 일시적으로
    치환한다. 원래 값은 반드시 복원한다.
    """
    original = ov.OBJECT_EC_SINGLE
    try:
        def diff(x):
            ov.OBJECT_EC_SINGLE = x
            no_tier = _total(base_scenario, ratio_r=ratio_r,
                             flash_days=base_scenario.retention_days,
                             site_config=ov.SITE_SINGLE)
            full_tier = _total(base_scenario, ratio_r=ratio_r, flash_days=1,
                               site_config=ov.SITE_SINGLE)
            return no_tier - full_tier
        crossings = find_crossings(diff, search_low, search_high, steps=300)
    finally:
        ov.OBJECT_EC_SINGLE = original

    if not crossings:
        return None
    return crossings[0].root


# =============================================================================
# 4. 2차원 비용 지도 (판단 ⑨)
# =============================================================================

@dataclass
class CostMap:
    """즉시검색 기간 x 배수 R 격자.

    오버헤드는 축으로 넣지 않는다. 값이 두 개뿐이라 격자가 아니라 곡선 두 개가
    되기 때문이다. 대신 지도를 사이트 구성별로 두 장 만든다.

    최소점 해석 시 주의
        격자의 최소 비용점은 항상 (최단 기간, 최저 배수) 에 위치한다.
        그러나 배수 R 은 선택 변수가 아니라 시장 조건이다. 따라서 최소점을
        "권고안"으로 읽으면 안 된다. 지도는 주어진 R 에서 기간을 얼마로 잡을
        때 비용이 어떻게 변하는지를 읽는 용도다. min_* 필드는 격자 자체의
        검산용이며 권고가 아니다.
    """
    site_config: str
    days_axis: list
    ratio_axis: list
    grid: list           # grid[i][j] = days_axis[i], ratio_axis[j] 의 총비용
    min_cost: float
    min_days: int
    min_ratio: float


def cost_map(
    base_scenario: cm.Scenario,
    site_config: str,
    days_axis=None,
    ratio_axis=None,
) -> CostMap:
    """2차원 비용 지도를 만든다."""
    if days_axis is None:
        days_axis = [1, 7, 14, 30, 60, 90, 120, 180, 270, 365, 540, 730]
    if ratio_axis is None:
        ratio_axis = [3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 22.0, 25.0, 30.0]

    grid = []
    best = (float("inf"), None, None)
    for d in days_axis:
        row = []
        for r in ratio_axis:
            v = _total(base_scenario, ratio_r=r, flash_days=d,
                       site_config=site_config)
            row.append(v)
            if v < best[0]:
                best = (v, d, r)
        grid.append(row)

    return CostMap(
        site_config=site_config,
        days_axis=list(days_axis),
        ratio_axis=list(ratio_axis),
        grid=grid,
        min_cost=best[0], min_days=best[1], min_ratio=best[2],
    )


def write_cost_map_csv(cmap: CostMap, path):
    """격자를 CSV 로 내보낸다. 차트의 원본 수치를 검증 가능하게 남긴다."""
    import csv
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([f"# site_config={cmap.site_config}"])
        w.writerow([f"# min_cost_krw={cmap.min_cost:.2f}",
                    f"min_days={cmap.min_days}", f"min_ratio={cmap.min_ratio}"])
        w.writerow(["flash_days"] + [f"R={r}" for r in cmap.ratio_axis])
        for d, row in zip(cmap.days_axis, cmap.grid):
            w.writerow([d] + [round(v, 2) for v in row])
    return path


# =============================================================================
# 내부 도우미
# =============================================================================

def _total(base: cm.Scenario, **overrides) -> float:
    """시나리오를 일부만 바꿔 5년 온프레미스 매체 원가를 구한다."""
    fields = {**base.__dict__, **overrides}
    years = fields.pop("years", 5)
    sc = cm.Scenario(**fields, years=years)
    return te.compute_tco(sc, terms=(years,)).term(years).onprem_krw


if __name__ == "__main__":
    from pricing_loader import load

    ls = load()
    baseline = cm.archive_baseline(ls.media)
    premium = ls.media.get_float("array_oem_premium", "base")

    base = cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90, years=5,
        archive_krw_per_tb=baseline, oem_premium=premium,
    )

    print("[breakeven.py] Phase 5 산출")
    print("100GB/day, 730일 보관, 5년, 색인DRR 2.0")
    print()

    print("### 1. 배수 R 손익분기 (기록용)")
    print("| 구성 | 손익분기 R | 관측 범위 내 | 비고 |")
    print("|---|---|---|---|")
    for site in (ov.SITE_SINGLE, ov.SITE_MULTI):
        rb = ratio_breakeven(base, site)
        root = f"{rb.root:.3f}" if rb.root is not None else "없음"
        print(f"| {site} | {root} | {'예' if rb.in_range else '**아니오**'} | {rb.note} |")
    print()

    print("### 2. 등비용 기간 — 90일 기준")
    print("| R | 단일 사이트 | 다중 사이트 | 손실 일수 | 가능 여부 |")
    print("|---|---|---|---|---|")
    for r in (3.0, 5.0, 10.0, 15.0, 22.0, 30.0):
        eq = equivalent_days(base, r, reference_days=90)
        print(f"| {r:4.1f} | 90일 | {eq.days}일 | {eq.lost_days}일 | "
              f"{'가능' if eq.feasible else '**불가능**'} |")
    print()

    print("### 2b. 임계 배수 — 3사이트 분산을 수용할 수 있는 최소 R")
    print("| 기준 즉시검색 | 임계 R | 관측 범위 내 |")
    print("|---|---|---|")
    for ref in (30, 60, 90, 180, 365, 730):
        crc = critical_ratio(base, ref)
        val = f"{crc.critical_r:.2f}" if crc.critical_r else "없음"
        print(f"| {ref}일 | **{val}** | {'예' if crc.in_range else '아니오'} |")
    print()

    print("### 3. 오버헤드 손익분기")
    for r in (3.0, 10.0, 22.0, 30.0):
        ob = overhead_breakeven(base, r)
        print(f"  R={r:4.1f} → 아카이브 오버헤드 {ob:.3f}배 초과 시 계층화 손해")
    print()

    print("### 4. 2차원 비용 지도")
    for site in (ov.SITE_SINGLE, ov.SITE_MULTI):
        cmap = cost_map(base, site)
        print(f"  [{site}] 최소 비용 {cmap.min_cost/1e6:,.2f}백만원 "
              f"(즉시검색 {cmap.min_days}일, R={cmap.min_ratio})")
