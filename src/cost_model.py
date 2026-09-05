"""
cost_model.py — 비용 계산 모듈 (Phase 4)

역할
    reduction.py 가 산출한 계층별 물리 용량(TB)과
    pricing_loader.py 가 제공하는 단가를 곱해 금액을 만든다.

    Phase 3 까지는 용량만 다뤘다. 이 모듈이 처음으로 돈을 산출한다.

설계 판단 ⑦ — 절대 단가를 고르지 않는다
    Phase 2 에서 대표값을 두지 않기로 확정했다(scan_range.base = null).
    시장 지수가 같은 분기에 세 계열을 동시에 발표하고 있어, 어느 계열을
    고르느냐가 곧 결론이 되기 때문이다.

    따라서 플래시 단가를 직접 고르지 않는다. 대신
        플래시 단가 = 배수 R x 아카이브 단가
    로 생성한다. R 을 3.0 에서 30.0 까지 훑으면 플래시 단가가 자동으로
    그 범위를 훑는다. "어느 지수를 믿느냐"가 결론에 영향을 주지 않는다.

    손익분기 배수 R 은 아카이브 단가의 절대값과 무관하다. 비용 비교가
    비율로 이뤄지기 때문이다. 기준선은 총액을 사람이 읽을 수 있게 하는
    역할만 한다.

설계 판단 ⑥ — 전액 취득 시점 계상
    매체 구매 대금은 계약 기간과 무관하게 1년차에 전액 나간다. 3년 계약에
    5년 상각을 적용해 3년치만 계상하면 실제로 나간 돈의 40%를 세지 않는
    셈이 된다. 선행 프로젝트의 오류 1번(기간 단위 혼동)과 같은 유형이다.

    잔존가치를 차감하는 방식은 채택하지 않았다. 3년 사용한 엔터프라이즈
    SSD 의 시장 가치를 산정할 근거가 없기 때문이다.

    다만 상각기간이 계약 기간보다 짧으면 재구매가 발생한다.
        재구매 횟수 = ceil(계약연수 / 상각연수)
    5년 계약에 5년 상각이면 1회, 4년 상각이면 2회다.
    이 관계가 상각기간을 민감도 대상으로 유지해야 하는 이유다.

두 경로를 합산하지 않는다
    온프레미스 경로는 자본적 지출(KRW/TB), 클라우드 경로는 운영비
    (KRW/GB/월)다. pricing_loader 의 Quantity 가 구조적으로 합산을 막지만,
    이 모듈도 함수를 분리해 두 경로가 섞이지 않게 한다.
"""

import math
from dataclasses import dataclass, field

import overhead as ov
import placement as pl
import reduction as rd
from pricing_loader import PricingError, Quantity

GB_PER_TB = 1000
MONTHS_PER_YEAR = 12


class CostModelError(Exception):
    """비용 계산 조건이 갖춰지지 않았을 때."""


# =============================================================================
# 온프레미스 경로 — 자본적 지출
# =============================================================================

@dataclass
class MediaPriceContext:
    """온프레미스 매체 단가 문맥.

    플래시 단가를 직접 받지 않는다. 아카이브 단가와 배수 R 로부터 생성한다.
    이것이 설계 판단 ⑦ 의 요점이다.
    """
    archive_krw_per_tb: float
    ratio_r: float
    amortization_years: int
    oem_premium: float = 1.0

    def __post_init__(self):
        if self.archive_krw_per_tb <= 0:
            raise CostModelError("아카이브 단가는 0보다 커야 합니다.")
        if self.ratio_r < 1.0:
            raise CostModelError(
                f"배수 R은 1.0 이상이어야 합니다. 받은 값: {self.ratio_r}. "
                "1.0 미만은 플래시가 아카이브보다 싸다는 뜻이며 "
                "본 프로젝트의 관측 범위(3.0~30.0) 밖입니다."
            )
        if self.amortization_years < 1:
            raise CostModelError("상각기간은 1년 이상이어야 합니다.")
        if self.oem_premium < 1.0:
            raise CostModelError("oem_premium은 1.0 이상이어야 합니다.")

    @property
    def flash_krw_per_tb(self) -> float:
        """플래시 단가는 고르지 않고 생성한다."""
        return self.archive_krw_per_tb * self.ratio_r * self.oem_premium

    @property
    def archive_effective_krw_per_tb(self) -> float:
        """프리미엄이 반영된 아카이브 단가.

        어레이 제조사 정품 프리미엄은 플래시와 아카이브 양쪽에 동일하게
        적용된다고 본다. 오브젝트 어플라이언스도 벤더 정품 드라이브를
        사용하기 때문이다. 다만 이는 도출이며 실측이 아니다.

        중요: 프리미엄이 양쪽에 같은 배수로 붙으면 배수 R 은 변하지 않는다.
        따라서 손익분기 R 은 프리미엄과 무관하며, 총액만 비례해서 커진다.
        """
        return self.archive_krw_per_tb * self.oem_premium


@dataclass
class MediaCost:
    """온프레미스 매체 원가 (KRW).

    '매체 원가'라는 표기가 중요하다. 총소유비용이 아니다. 유지보수 계약료,
    설치 인건비, 전력, 랙 공간은 범위에서 제외했다(phase00_scope.md 8절).
    """
    flash_krw: float
    archive_krw: float
    purchase_count: int
    years: int
    total_krw: float = field(init=False)

    def __post_init__(self):
        self.total_krw = round(self.flash_krw + self.archive_krw, 2)


def media_cost(
    physical: rd.ReductionResult,
    ctx: MediaPriceContext,
    years: int,
) -> MediaCost:
    """계층별 물리 용량에 단가를 곱해 매체 원가를 산출한다.

    Parameters
    ----------
    physical : ReductionResult
        reduction.apply_reduction() 의 출력. 물리 용량이어야 한다.
    ctx : MediaPriceContext
        아카이브 단가, 배수 R, 상각기간.
    years : int
        계약 기간. 3 또는 5.

    Returns
    -------
    MediaCost
    """
    if years < 1:
        raise CostModelError("years는 1 이상이어야 합니다.")

    # 상각기간이 계약 기간보다 짧으면 재구매가 발생한다.
    count = math.ceil(years / ctx.amortization_years)

    flash = physical.flash_physical_tb * ctx.flash_krw_per_tb * count
    archive = physical.archive_physical_tb * ctx.archive_effective_krw_per_tb * count

    return MediaCost(
        flash_krw=round(flash, 2),
        archive_krw=round(archive, 2),
        purchase_count=count,
        years=years,
    )


# =============================================================================
# 클라우드 경로 — 운영비 (대조군)
# =============================================================================

@dataclass
class CloudCost:
    """클라우드 서비스 이용료 (KRW).

    온프레미스 매체 원가와 합산하거나 평균하지 않는다. 단위와 비용 성격이
    다르기 때문이다. 두 값은 나란히 제시하되 더하지 않는다.
    """
    flash_krw: float
    archive_krw: float
    years: int
    total_krw: float = field(init=False)

    def __post_init__(self):
        self.total_krw = round(self.flash_krw + self.archive_krw, 2)


def cloud_cost(
    physical: rd.ReductionResult,
    flash_krw_per_gb_month: float,
    archive_krw_per_gb_month: float,
    years: int,
) -> CloudCost:
    """클라우드 요금제로 같은 용량을 담을 때의 이용료.

    대조 경로다. 주 경로(온프레미스)와 합산하지 않는다.
    """
    if years < 1:
        raise CostModelError("years는 1 이상이어야 합니다.")

    months = years * MONTHS_PER_YEAR
    flash_gb = physical.flash_physical_tb * GB_PER_TB
    archive_gb = physical.archive_physical_tb * GB_PER_TB

    return CloudCost(
        flash_krw=round(flash_gb * flash_krw_per_gb_month * months, 2),
        archive_krw=round(archive_gb * archive_krw_per_gb_month * months, 2),
        years=years,
    )


# =============================================================================
# 시나리오 — 한 번에 계산하기 위한 묶음
# =============================================================================

@dataclass
class Scenario:
    """계산 조건. 손익분기 탐색과 민감도 분석은 이 값들을 바꿔 반복 실행한다."""
    daily_gb: float
    ratio_r: float
    flash_days: int
    years: int = 5
    retention_days: int = pl.DEFAULT_RETENTION_DAYS
    rf: int = 1
    sf: int = 1
    archive_copies: int = 1
    site_config: str = ov.SITE_SINGLE
    compression_state: rd.CompressionState = rd.CompressionState.HOST_COMPRESSED
    drr_tsidx: float = None
    archive_krw_per_tb: float = None
    amortization_years: int = 5
    oem_premium: float = 1.0

    def __post_init__(self):
        if self.site_config not in ov.VALID_SITE_CONFIGS:
            raise CostModelError(
                f"site_config는 {ov.VALID_SITE_CONFIGS} 중 하나여야 합니다."
            )


def evaluate(sc: Scenario) -> dict:
    """시나리오 하나를 끝까지 계산한다.

    파이프라인
        배치 → 감축 → 오버헤드 → 단가 → 금액

    Returns
    -------
    dict
        placement, physical, cost 를 함께 담아 돌려준다. 중간 결과를 버리지
        않는 이유는 검산과 차트 작성에 필요하기 때문이다.
    """
    if sc.archive_krw_per_tb is None:
        raise CostModelError(
            "archive_krw_per_tb를 명시해야 합니다. 기준선이 없으면 "
            "플래시 단가를 생성할 수 없습니다."
        )

    lifecycle = pl.LifecyclePolicy(
        retention_days=sc.retention_days,
        flash_days=sc.flash_days,
    )
    replication = pl.ReplicationPolicy(
        rf=sc.rf, sf=sc.sf, archive_copies=sc.archive_copies,
    )
    placement = pl.compute_placement(sc.daily_gb, lifecycle, replication)

    policy = rd.ReductionPolicy(
        compression_state=sc.compression_state,
        drr_tsidx=sc.drr_tsidx,
    )
    cfg = ov.OverheadConfig(site_config=sc.site_config)
    physical = rd.apply_reduction(placement, policy, cfg)

    ctx = MediaPriceContext(
        archive_krw_per_tb=sc.archive_krw_per_tb,
        ratio_r=sc.ratio_r,
        amortization_years=sc.amortization_years,
        oem_premium=sc.oem_premium,
    )
    cost = media_cost(physical, ctx, sc.years)

    return {
        "scenario": sc,
        "lifecycle": lifecycle,
        "placement": placement,
        "physical": physical,
        "price": ctx,
        "cost": cost,
    }


def archive_baseline(ledger, which="base") -> float:
    """아카이브 단가 기준선을 원장에서 꺼내 원화로 환산한다.

    판단 ⑦ 에 따라 소매 니어라인 단가를 기준선으로 쓴다. 대상이 국내
    중견기업이므로 하이퍼스케일 대량 계약가보다 소매가가 현실에 가깝다.
    제조사 실현 단가는 '대량 구매 시 어디까지 내려갈 수 있는가'를 보여주는
    하한으로만 참조한다.
    """
    if which == "low":
        # 하이퍼스케일 대량 계약 수준
        q = ledger.get_krw("hdd_manufacturer_realized", "low")
    else:
        q = ledger.get_krw("hdd_retail_nearline", "base")
    return float(q)


if __name__ == "__main__":
    from pricing_loader import load

    ls = load()
    baseline = archive_baseline(ls.media)
    lower = archive_baseline(ls.media, "low")

    print("[cost_model.py] 검산")
    print(f"  아카이브 기준선(소매)   : {baseline:,.0f} KRW/TB")
    print(f"  아카이브 하한(대량 계약) : {lower:,.0f} KRW/TB")
    print(f"  환율 기준               : {ls.media.fx_basis}")
    print()

    print("100GB/day, 730일 보관, 90일 즉시검색, 5년, 단일 사이트")
    print()
    print("| 배수 R | 플래시 단가 | 플래시 물리 | 아카이브 물리 | 플래시 비용 | 아카이브 비용 | 합계 |")
    print("|---|---|---|---|---|---|---|")
    for r in (3.0, 5.0, 10.0, 22.0, 30.0):
        out = evaluate(Scenario(
            daily_gb=100, ratio_r=r, flash_days=90, years=5,
            archive_krw_per_tb=baseline,
        ))
        c, p, pr = out["cost"], out["physical"], out["price"]
        print(f"| {r:4.1f} | {pr.flash_krw_per_tb:,.0f} | "
              f"{p.flash_physical_tb:.2f} TB | {p.archive_physical_tb:.2f} TB | "
              f"{c.flash_krw/1e6:,.1f}백만 | {c.archive_krw/1e6:,.1f}백만 | "
              f"**{c.total_krw/1e6:,.1f}백만** |")

    print()
    print("[상각기간에 따른 재구매 횟수]")
    for years in (3, 5):
        for am in (4, 5, 6):
            out = evaluate(Scenario(
                daily_gb=100, ratio_r=22.0, flash_days=90, years=years,
                amortization_years=am, archive_krw_per_tb=baseline,
            ))
            c = out["cost"]
            print(f"  {years}년 계약 / {am}년 상각 → 구매 {c.purchase_count}회, "
                  f"{c.total_krw/1e6:,.1f}백만원")
