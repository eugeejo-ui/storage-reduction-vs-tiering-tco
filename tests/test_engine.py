"""
test_engine.py — Phase 4 비용 엔진 검증

핵심 목적
    1. 단위계가 다른 두 원장의 값이 섞이지 않는가 (리스크 R7)
    2. 미채택·미확보 항목이 조용히 계산에 들어가지 않는가
    3. 부분 합이 총액과 일치하는가 (선행 프로젝트 오류 5번)
    4. 배수 R 이 실제로 결과를 움직이는가
    5. 상각기간이 재구매 횟수에 반영되는가
"""

from pathlib import Path

import pytest

import cost_model as cm
import overhead as ov
import placement as pl
import reduction as rd
import tco_engine as te
from pricing_loader import (
    Ledger, LedgerSet, PricingError, UnitSystemError, Quantity, load,
    LEDGER_MEDIA, LEDGER_CLOUD,
)


@pytest.fixture(scope="module")
def ls():
    here = Path(__file__).resolve().parent
    return load(here.parent / "data")


@pytest.fixture(scope="module")
def baseline(ls):
    return cm.archive_baseline(ls.media)


# =============================================================================
# 필수 검증 1 — 단위계 분리 (리스크 R7)
# =============================================================================

def test_단위계_두_원장의_값을_더하면_예외(ls):
    """온프레미스 매체 구매 원가와 클라우드 서비스 이용료는 단위와
    비용 성격이 다르므로 합산할 수 없다.
    """
    m = ls.media.get_krw("hdd_manufacturer_realized")
    c = ls.cloud.get("object_price")
    with pytest.raises(UnitSystemError, match="단위계"):
        _ = m + c


def test_단위계_두_원장의_값을_빼도_예외(ls):
    m = ls.media.get_krw("hdd_retail_nearline")
    c = ls.cloud.get("ssd_price")
    with pytest.raises(UnitSystemError):
        _ = m - c


def test_단위계_원장별_ledger_type이_달라야_한다(ls):
    assert ls.media.ledger_type == LEDGER_MEDIA
    assert ls.cloud.ledger_type == LEDGER_CLOUD
    assert ls.media.unit_system != ls.cloud.unit_system


def test_단위계_두_원장에_같은_키가_없어야_한다(ls):
    """이름이 같으면 어느 원장의 값인지 혼동이 발생한다."""
    assert ls.overlapping_keys() == []


def test_단위계_Quantity끼리_곱하면_예외(ls):
    """곱하면 단위가 바뀌므로 호출부가 결과 단위를 명시해야 한다."""
    a = ls.media.get("hdd_retail_nearline")
    b = ls.media.get("hdd_retail_nearline")
    with pytest.raises(UnitSystemError, match="곱할 수 없습니다"):
        _ = a * b


def test_단위계_스칼라_배는_허용(ls):
    q = ls.media.get("hdd_retail_nearline")
    assert (q * 2).amount == pytest.approx(q.amount * 2)
    assert (q * 2).unit == q.unit


def test_단위계_무차원_항목에_get_float_사용_가능(ls):
    assert ls.media.get_float("array_oem_premium", "base") == 3.3
    assert ls.media.get_float("media_amortization_years", "base") == 5


def test_단위계_단위있는_항목에_get_float_쓰면_예외(ls):
    with pytest.raises(UnitSystemError, match="단위"):
        ls.media.get_float("hdd_retail_nearline")


def test_단위계_잘못된_원장_조합은_생성_불가(ls):
    """매체 원장 자리에 클라우드 원장을 넣으면 걸러야 한다."""
    with pytest.raises(PricingError, match="ledger_type"):
        LedgerSet(ls.cloud, ls.cloud)


# =============================================================================
# 필수 검증 2 — 사용 불가 항목 차단
# =============================================================================

def test_차단_pending_항목은_예외(ls):
    with pytest.raises(PricingError, match="pending"):
        ls.media.get("object_appliance_media_only")


def test_차단_미채택_항목은_예외(ls):
    """Phase 2 에서 제조사 재무제표와 모순되어 미채택한 지수값이
    조용히 계산에 들어가면 안 된다.
    """
    with pytest.raises(PricingError, match="미채택"):
        ls.media.get("hdd_vendor_index")


def test_차단_미채택_항목은_참조는_가능(ls):
    """계산에는 못 쓰지만 값 자체는 기록으로 남아 있어야 한다."""
    it = ls.media.item("hdd_vendor_index")
    assert it.adopted is False
    assert it.rejection_reason.strip() != ""
    assert it.low == 16.5


def test_차단_미채택_항목은_민감도_대상에서_제외(ls):
    keys = [k for k, _ in ls.media.sensitivity_targets()]
    assert "hdd_vendor_index" not in keys


def test_차단_없는_키는_예외(ls):
    with pytest.raises(PricingError, match="없습니다"):
        ls.media.get("no_such_key_12345")


def test_차단_대표값_없는_항목은_base_조회시_예외(ls):
    """scan_range 의 base 를 null 로 둔 것이 설계 의도다.
    대표값을 쓰려는 시도가 차단되어야 한다.
    """
    with pytest.raises(PricingError, match="비어 있습니다"):
        ls.media.get("scan_range", "base")


def test_차단_대표값_없어도_low_high는_조회_가능(ls):
    assert ls.media.get("scan_range", "low").amount == 3.0
    assert ls.media.get("scan_range", "high").amount == 30.0


# =============================================================================
# 필수 검증 3 — 부분 합 일치
# =============================================================================

def test_합계_온프레미스_부분합이_총액과_일치(baseline):
    res = te.compute_tco(cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90, archive_krw_per_tb=baseline,
    ), terms=(5,))
    t = res.term(5)
    assert t.onprem_flash_krw + t.onprem_archive_krw == pytest.approx(t.onprem_krw)


def test_합계_클라우드_부분합이_총액과_일치(ls, baseline):
    cf, ca = te.cloud_prices_krw(ls.cloud)
    res = te.compute_tco(
        cm.Scenario(daily_gb=100, ratio_r=22.0, flash_days=90,
                    archive_krw_per_tb=baseline),
        terms=(5,),
        cloud_flash_krw_per_gb_month=cf,
        cloud_archive_krw_per_gb_month=ca,
    )
    t = res.term(5)
    assert t.cloud_flash_krw + t.cloud_archive_krw == pytest.approx(t.cloud_krw)


def test_합계_불일치시_생성_단계에서_예외():
    """부분 합이 어긋난 결과 객체는 만들어질 수 없어야 한다."""
    with pytest.raises(te.TcoError, match="일치하지 않습니다"):
        te.TermResult(
            years=5, onprem_krw=100.0,
            onprem_flash_krw=60.0, onprem_archive_krw=30.0,  # 합 90
            purchase_count=1,
        )


def test_합계_클라우드_단가를_한쪽만_주면_예외(baseline):
    with pytest.raises(te.TcoError, match="두 계층"):
        te.compute_tco(
            cm.Scenario(daily_gb=100, ratio_r=22.0, flash_days=90,
                        archive_krw_per_tb=baseline),
            cloud_flash_krw_per_gb_month=100.0,
        )


# =============================================================================
# 필수 검증 4 — 배수 R 이 결과를 움직이는가
# =============================================================================

def test_배수_R을_바꾸면_비용이_달라져야_한다(baseline):
    def total(r):
        return te.compute_tco(cm.Scenario(
            daily_gb=100, ratio_r=r, flash_days=90, archive_krw_per_tb=baseline,
        ), terms=(5,)).term(5).onprem_krw

    assert total(3.0) < total(10.0) < total(30.0)


def test_배수_플래시_단가는_R로_생성된다(baseline):
    ctx = cm.MediaPriceContext(
        archive_krw_per_tb=baseline, ratio_r=22.0, amortization_years=5,
    )
    assert ctx.flash_krw_per_tb == pytest.approx(baseline * 22.0)


def test_배수_1미만은_예외(baseline):
    """관측 범위(3.0~30.0) 밖이며 플래시가 아카이브보다 싸다는 뜻이다."""
    with pytest.raises(cm.CostModelError, match="1.0 이상"):
        cm.MediaPriceContext(
            archive_krw_per_tb=baseline, ratio_r=0.5, amortization_years=5,
        )


def test_배수_프리미엄은_양_계층에_같이_적용된다(baseline):
    """프리미엄이 같은 배수로 붙으면 배수 R 은 변하지 않는다.
    따라서 손익분기 R 은 프리미엄과 무관하고 총액만 비례해서 커진다.
    """
    plain = cm.MediaPriceContext(baseline, 22.0, 5, oem_premium=1.0)
    prem = cm.MediaPriceContext(baseline, 22.0, 5, oem_premium=3.3)

    ratio_plain = plain.flash_krw_per_tb / plain.archive_effective_krw_per_tb
    ratio_prem = prem.flash_krw_per_tb / prem.archive_effective_krw_per_tb
    assert ratio_plain == pytest.approx(ratio_prem)

    # 총액은 프리미엄 배수만큼 커진다
    assert prem.flash_krw_per_tb == pytest.approx(plain.flash_krw_per_tb * 3.3)


# =============================================================================
# 필수 검증 5 — 상각과 계약 기간
# =============================================================================

def test_상각_계약보다_짧으면_재구매가_발생(baseline):
    """5년 계약에 4년 상각이면 2회 구매해야 한다."""
    def count(years, am):
        return te.compute_tco(cm.Scenario(
            daily_gb=100, ratio_r=22.0, flash_days=90,
            amortization_years=am, archive_krw_per_tb=baseline,
        ), terms=(years,)).term(years).purchase_count

    assert count(5, 5) == 1
    assert count(5, 4) == 2
    assert count(3, 5) == 1
    assert count(3, 6) == 1


def test_상각_전액_취득시점_계상이므로_3년과_5년이_같다(baseline):
    """매체 구매 대금은 계약 기간과 무관하게 1년차에 전액 나간다.
    상각기간이 계약 기간보다 길면 3년과 5년의 매체 원가가 같아야 한다.
    """
    res = te.compute_tco(cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90,
        amortization_years=5, archive_krw_per_tb=baseline,
    ))
    assert res.term(3).onprem_krw == pytest.approx(res.term(5).onprem_krw)


def test_상각_클라우드는_기간에_비례해야_한다(ls, baseline):
    """운영비이므로 5년이 3년의 5/3 배여야 한다."""
    cf, ca = te.cloud_prices_krw(ls.cloud)
    res = te.compute_tco(
        cm.Scenario(daily_gb=100, ratio_r=22.0, flash_days=90,
                    archive_krw_per_tb=baseline),
        cloud_flash_krw_per_gb_month=cf,
        cloud_archive_krw_per_gb_month=ca,
    )
    assert res.term(5).cloud_krw / res.term(3).cloud_krw == pytest.approx(5 / 3)


def test_상각_0년은_예외(baseline):
    with pytest.raises(cm.CostModelError, match="상각기간"):
        cm.MediaPriceContext(baseline, 22.0, 0)


# =============================================================================
# 통합 검증
# =============================================================================

def test_통합_계층화가_진행되면_비용이_줄어야_한다(baseline):
    """단일 사이트, R=22 기준. 방향이 뒤집히면 계산 어딘가가 잘못된 것이다."""
    prev = None
    for d in (730, 365, 180, 90, 30, 1):
        total = te.compute_tco(cm.Scenario(
            daily_gb=100, ratio_r=22.0, flash_days=d, archive_krw_per_tb=baseline,
        ), terms=(5,)).term(5).onprem_krw
        if prev is not None:
            assert total < prev
        prev = total


def test_통합_기준선을_바꿔도_손익분기_구조는_유지된다(baseline):
    """아카이브 단가는 총액 스케일만 바꾼다. 비용 비율은 그대로여야 한다."""
    def shares(base_price):
        t = te.compute_tco(cm.Scenario(
            daily_gb=100, ratio_r=22.0, flash_days=90,
            archive_krw_per_tb=base_price,
        ), terms=(5,)).term(5)
        return t.onprem_flash_share

    assert shares(baseline) == pytest.approx(shares(baseline * 3))


def test_통합_아카이브_단가_미지정시_예외():
    with pytest.raises(cm.CostModelError, match="archive_krw_per_tb"):
        cm.evaluate(cm.Scenario(daily_gb=100, ratio_r=22.0, flash_days=90))


def test_통합_다중사이트가_아카이브_비용을_늘려야_한다(baseline):
    """Phase 3 에서 확인한 오버헤드 차이가 비용에 반영되는지 확인한다."""
    def archive_cost(site):
        return te.compute_tco(cm.Scenario(
            daily_gb=100, ratio_r=22.0, flash_days=90, site_config=site,
            archive_krw_per_tb=baseline,
        ), terms=(5,)).term(5).onprem_archive_krw

    single = archive_cost(ov.SITE_SINGLE)
    multi = archive_cost(ov.SITE_MULTI)
    assert multi / single == pytest.approx(ov.OBJECT_MULTISITE / ov.OBJECT_EC_SINGLE, rel=1e-6)


def test_통합_환율이_원장에_고정되어_있어야_한다(ls):
    """Phase 2 에서 기준일을 고정했다. 조회 시점에 따라 값이 변하면 안 된다."""
    assert ls.media.fx_rate == 1350.4
    assert "2026-09-04" in ls.media.fx_basis
