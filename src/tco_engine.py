"""
tco_engine.py — 총비용 엔진 (Phase 4)

역할
    cost_model.py 가 산출한 한 시나리오의 금액을 받아
    계약 기간별로 정리하고, 두 경로(온프레미스·클라우드)를 나란히 제시한다.

    "나란히"가 핵심이다. 합산하지 않는다.

선행 프로젝트에서 승계한 판단 — 3년·5년 병행
    단일 기간만 계산하면 자체 구축에 불리한 편향이 생긴다는 것을 선행
    프로젝트에서 확인했다. 본 프로젝트도 같은 이유로 두 기간을 병행한다.

    다만 본 프로젝트에서는 편향의 방향이 다르다. 매체 구매 대금은 계약
    기간과 무관하게 1년차에 전액 나가므로, 계약이 짧을수록 온프레미스가
    불리해진다. 이는 계산 오류가 아니라 자본적 지출의 구조다.

두 경로를 합산하지 않는 이유 (phase00_scope.md 4.2절)
    온프레미스는 자본적 지출(KRW/TB 일시 구매), 클라우드는 운영비
    (KRW/GB/월 반복)다. 단위와 비용 성격이 다르다. 또한 클라우드 정가는
    NAND 계약가 변동을 즉시 반영하지 않는 반면 매체 구매가는 즉시 반영하므로,
    동일 시점에도 두 경로의 배수가 다르게 나타난다.

    두 경로의 결과가 다르게 나온다면 그 차이 자체가 결과다. 동일 시점에
    온프레미스 조달과 클라우드 임차의 상대적 유불리가 이동했다는 뜻이기
    때문이다.
"""

from dataclasses import dataclass, field

import cost_model as cm
from pricing_loader import PricingError

GB_PER_TB = 1000
MONTHS_PER_YEAR = 12
STANDARD_TERMS = (3, 5)


class TcoError(Exception):
    """총비용 산출 조건이 갖춰지지 않았을 때."""


@dataclass
class TermResult:
    """계약 기간 하나에 대한 결과."""
    years: int
    onprem_krw: float
    onprem_flash_krw: float
    onprem_archive_krw: float
    purchase_count: int
    cloud_krw: float = None
    cloud_flash_krw: float = None
    cloud_archive_krw: float = None

    def __post_init__(self):
        # 부분 합이 총액과 일치하는지 즉시 확인한다.
        # 선행 프로젝트에서 차트의 부분 합이 총액과 어긋난 오류가 있었으므로,
        # 계산 단계에서부터 막는다.
        parts = round(self.onprem_flash_krw + self.onprem_archive_krw, 2)
        if abs(parts - self.onprem_krw) > 0.01:
            raise TcoError(
                f"온프레미스 부분 합({parts:,.2f})이 총액({self.onprem_krw:,.2f})과 "
                f"일치하지 않습니다."
            )
        if self.cloud_krw is not None:
            cparts = round(self.cloud_flash_krw + self.cloud_archive_krw, 2)
            if abs(cparts - self.cloud_krw) > 0.01:
                raise TcoError(
                    f"클라우드 부분 합({cparts:,.2f})이 총액({self.cloud_krw:,.2f})과 "
                    f"일치하지 않습니다."
                )

    @property
    def onprem_flash_share(self) -> float:
        if self.onprem_krw == 0:
            return 0.0
        return self.onprem_flash_krw / self.onprem_krw


@dataclass
class TcoResult:
    """시나리오 하나에 대한 기간별 총비용."""
    scenario: cm.Scenario
    terms: dict = field(default_factory=dict)
    physical_flash_tb: float = 0.0
    physical_archive_tb: float = 0.0
    flash_krw_per_tb: float = 0.0
    archive_krw_per_tb: float = 0.0
    blended_drr: float = 0.0

    def term(self, years: int) -> TermResult:
        if years not in self.terms:
            raise TcoError(
                f"{years}년 결과가 없습니다. 계산된 기간: {sorted(self.terms)}"
            )
        return self.terms[years]


def compute_tco(
    sc: cm.Scenario,
    terms=STANDARD_TERMS,
    cloud_flash_krw_per_gb_month: float = None,
    cloud_archive_krw_per_gb_month: float = None,
) -> TcoResult:
    """시나리오 하나를 기간별로 계산한다.

    Parameters
    ----------
    sc : Scenario
        계산 조건. years 필드는 무시하고 terms 를 사용한다.
    terms : tuple
        계약 기간 목록. 기본 (3, 5).
    cloud_flash_krw_per_gb_month, cloud_archive_krw_per_gb_month : float
        둘 다 주어지면 대조 경로를 함께 계산한다. 생략하면 온프레미스만.

    Returns
    -------
    TcoResult
    """
    want_cloud = (cloud_flash_krw_per_gb_month is not None
                  and cloud_archive_krw_per_gb_month is not None)
    if (cloud_flash_krw_per_gb_month is None) != (cloud_archive_krw_per_gb_month is None):
        raise TcoError(
            "클라우드 단가는 두 계층을 함께 주거나 둘 다 생략해야 합니다. "
            "한쪽만 주면 계층 간 비교가 성립하지 않습니다."
        )

    result = TcoResult(scenario=sc)

    for years in terms:
        sc_y = cm.Scenario(**{**sc.__dict__, "years": years})
        out = cm.evaluate(sc_y)
        phys, cost, price = out["physical"], out["cost"], out["price"]

        cloud = None
        if want_cloud:
            cloud = cm.cloud_cost(
                phys,
                cloud_flash_krw_per_gb_month,
                cloud_archive_krw_per_gb_month,
                years,
            )

        result.terms[years] = TermResult(
            years=years,
            onprem_krw=cost.total_krw,
            onprem_flash_krw=cost.flash_krw,
            onprem_archive_krw=cost.archive_krw,
            purchase_count=cost.purchase_count,
            cloud_krw=cloud.total_krw if cloud else None,
            cloud_flash_krw=cloud.flash_krw if cloud else None,
            cloud_archive_krw=cloud.archive_krw if cloud else None,
        )

        # 용량과 단가는 기간과 무관하므로 마지막 값을 대표로 저장한다.
        result.physical_flash_tb = phys.flash_physical_tb
        result.physical_archive_tb = phys.archive_physical_tb
        result.flash_krw_per_tb = price.flash_krw_per_tb
        result.archive_krw_per_tb = price.archive_effective_krw_per_tb
        result.blended_drr = phys.flash_blended_drr

    return result


def cloud_prices_krw(ledger) -> tuple:
    """클라우드 원장에서 대조 경로 단가를 꺼내 원화로 환산한다.

    플래시 대응 계층은 블록 스토리지, 아카이브 대응 계층은 오브젝트
    스토리지로 본다. 두 값은 클라우드 원장에서만 꺼내며, 매체 원장의
    값과 섞지 않는다.
    """
    flash = float(ledger.get_krw("ssd_price")) / GB_PER_TB * GB_PER_TB
    archive = float(ledger.get_krw("object_price")) / GB_PER_TB * GB_PER_TB
    # 위 연산은 항등이며 단위를 명시하기 위한 표기다.
    # 원장 단위가 KRW/GB/월이므로 그대로 반환한다.
    return flash, archive


if __name__ == "__main__":
    from pricing_loader import load

    ls = load()
    baseline = cm.archive_baseline(ls.media)
    premium = ls.media.get_float("array_oem_premium", "base")
    cf, ca = cloud_prices_krw(ls.cloud)

    print("[tco_engine.py] 검산")
    print(f"  아카이브 기준선 : {baseline:,.0f} KRW/TB")
    print(f"  어레이 정품 프리미엄 : {premium}배")
    print(f"  클라우드 블록   : {cf:,.2f} KRW/GB/월")
    print(f"  클라우드 오브젝트: {ca:,.2f} KRW/GB/월")
    print()

    sc = cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90,
        archive_krw_per_tb=baseline, oem_premium=premium,
    )
    res = compute_tco(sc, cloud_flash_krw_per_gb_month=cf,
                      cloud_archive_krw_per_gb_month=ca)

    print("100GB/day, 730일 보관, 90일 즉시검색, R=22, 단일 사이트")
    print(f"  플래시 물리 {res.physical_flash_tb:.2f} TB / "
          f"아카이브 물리 {res.physical_archive_tb:.2f} TB")
    print()
    print("| 기간 | 온프레미스 매체 원가 | 구매 횟수 | 클라우드 이용료 |")
    print("|---|---|---|---|")
    for y in STANDARD_TERMS:
        t = res.term(y)
        print(f"| {y}년 | {t.onprem_krw/1e6:,.1f}백만 | {t.purchase_count}회 | "
              f"{t.cloud_krw/1e6:,.1f}백만 |")
    print()
    print("※ 두 열을 합산하거나 평균하지 않는다. 단위와 비용 성격이 다르다.")
    print("   온프레미스는 매체 원가이며 총소유비용이 아니다.")
    print("   (유지보수·설치·전력·랙은 범위에서 제외)")
    print()

    print("[계층화 진행에 따른 5년 온프레미스 매체 원가]")
    print("| 즉시검색 | 플래시 물리 | 아카이브 물리 | 플래시 비용 | 아카이브 비용 | 합계 |")
    print("|---|---|---|---|---|---|")
    for d in (730, 365, 180, 90, 30, 1):
        r = compute_tco(cm.Scenario(
            daily_gb=100, ratio_r=22.0, flash_days=d,
            archive_krw_per_tb=baseline, oem_premium=premium,
        ), terms=(5,))
        t = r.term(5)
        print(f"| {d}일 | {r.physical_flash_tb:.2f} TB | {r.physical_archive_tb:.2f} TB | "
              f"{t.onprem_flash_krw/1e6:,.2f}백만 | {t.onprem_archive_krw/1e6:,.2f}백만 | "
              f"**{t.onprem_krw/1e6:,.2f}백만** |")
