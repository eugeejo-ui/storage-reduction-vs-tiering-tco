"""
test_breakeven.py — Phase 5 손익분기 검증

핵심 목적
    1. 계단 구간을 놓치지 않는가 (전 구간 스캔 + 이분법)
    2. 이분법 결과가 전 구간 스캔과 일치하는가
    3. 손익분기가 아카이브 단가 기준선과 무관한가 (Phase 4 성질 유지)
    4. 격자의 최소점이 개별 계산과 일치하는가
    5. 두 경로의 손익분기가 하나로 합쳐지지 않는가
"""

from pathlib import Path

import pytest

import breakeven as bk
import cost_model as cm
import overhead as ov
import tco_engine as te
from pricing_loader import load


@pytest.fixture(scope="module")
def ls():
    here = Path(__file__).resolve().parent
    return load(here.parent / "data")


@pytest.fixture(scope="module")
def base(ls):
    return cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90, years=5,
        archive_krw_per_tb=cm.archive_baseline(ls.media),
        oem_premium=ls.media.get_float("array_oem_premium", "base"),
    )


# =============================================================================
# 필수 검증 1 — 계단 구간 탐지
# =============================================================================

def test_계단_부호변화를_모두_찾아야_한다():
    """교차가 두 번 있는 함수에서 하나만 찾으면 실패다.
    이분법만 쓰면 반드시 하나만 찾는다.
    """
    f = lambda x: (x - 2.0) * (x - 5.0)   # 2와 5에서 교차
    roots = sorted(c.root for c in bk.find_crossings(f, 0.0, 8.0, steps=200))
    assert len(roots) == 2
    assert roots[0] == pytest.approx(2.0, abs=1e-3)
    assert roots[1] == pytest.approx(5.0, abs=1e-3)


def test_계단_상각기간_계단이_비용에_반영된다(base):
    """재구매 횟수는 ceil() 이므로 정수 경계에서 계단이 생긴다."""
    def total(am):
        sc = cm.Scenario(**{**base.__dict__, "amortization_years": am})
        return te.compute_tco(sc, terms=(5,)).term(5).onprem_krw

    # 4년 상각은 2회 구매, 5년 상각은 1회 구매
    assert total(4) == pytest.approx(total(5) * 2)
    assert total(5) == total(6)


def test_계단_탐색범위가_뒤집히면_예외():
    with pytest.raises(bk.BreakevenError, match="탐색 범위"):
        bk.find_crossings(lambda x: x, 10.0, 1.0)


def test_계단_교차가_없으면_빈_목록():
    assert bk.find_crossings(lambda x: x + 100, 0.0, 10.0) == []


# =============================================================================
# 필수 검증 2 — 이분법 결과가 전 구간 스캔과 일치
# =============================================================================

def test_수렴_이분법_결과가_스캔_결과와_일치(base):
    """임계 R 이 실제로 부호가 바뀌는 지점인지 직접 확인한다."""
    crc = bk.critical_ratio(base, reference_days=90)
    r = crc.critical_r
    assert r is not None

    def gap(x):
        ref = bk._total(base, ratio_r=x, flash_days=90, site_config=ov.SITE_SINGLE)
        tgt = bk._total(base, ratio_r=x, flash_days=1, site_config=ov.SITE_MULTI)
        return ref - tgt

    assert gap(r - 0.5) < 0    # 조금 아래에서는 수용 불가
    assert gap(r + 0.5) > 0    # 조금 위에서는 수용 가능


def test_수렴_등비용_기간이_실제로_등비용인가(base):
    """산출된 일수에서 두 구성의 비용이 실제로 근접해야 한다."""
    eq = bk.equivalent_days(base, ratio_r=22.0, reference_days=90)
    assert eq.feasible

    target = bk._total(base, ratio_r=22.0, flash_days=eq.days,
                       site_config=ov.SITE_MULTI)
    assert target <= eq.reference_cost                       # 예산 이내
    over = bk._total(base, ratio_r=22.0, flash_days=eq.days + 5,
                     site_config=ov.SITE_MULTI)
    assert over > eq.reference_cost                          # 조금 늘리면 초과


def test_수렴_불가능_구간은_0일로_보고(base):
    """R 이 낮으면 어떤 기간으로도 예산을 맞출 수 없다."""
    eq = bk.equivalent_days(base, ratio_r=3.0, reference_days=90)
    assert eq.days == 0
    assert eq.feasible is False


# =============================================================================
# 필수 검증 3 — 기준선 무관성 (Phase 4 성질 유지)
# =============================================================================

def test_무관성_손익분기는_아카이브_단가와_무관해야_한다(base):
    """비용 비교가 비율로 이뤄지므로 기준선은 총액 스케일만 바꾼다.
    기준선을 바꿨는데 임계 R 이 변하면 계산 어딘가가 잘못된 것이다.
    """
    def crit(price):
        sc = cm.Scenario(**{**base.__dict__, "archive_krw_per_tb": price})
        return bk.critical_ratio(sc, reference_days=90).critical_r

    p = base.archive_krw_per_tb
    assert crit(p) == pytest.approx(crit(p * 3), rel=1e-6)
    assert crit(p) == pytest.approx(crit(p * 0.5), rel=1e-6)


def test_무관성_손익분기는_정품_프리미엄과도_무관(base):
    """프리미엄이 양 계층에 같은 배수로 붙으므로 비율이 유지된다."""
    def crit(premium):
        sc = cm.Scenario(**{**base.__dict__, "oem_premium": premium})
        return bk.critical_ratio(sc, reference_days=90).critical_r

    assert crit(1.0) == pytest.approx(crit(3.9), rel=1e-6)


def test_무관성_손익분기는_일일_로그량과도_무관(base):
    """모든 항이 로그량에 비례하므로 비율이 유지된다."""
    def crit(gb):
        sc = cm.Scenario(**{**base.__dict__, "daily_gb": gb})
        return bk.critical_ratio(sc, reference_days=90).critical_r

    assert crit(10) == pytest.approx(crit(500), rel=1e-6)


# =============================================================================
# 필수 검증 4 — 격자 검산
# =============================================================================

def test_격자_최소점이_개별_계산과_일치(base):
    cmap = bk.cost_map(base, ov.SITE_SINGLE)
    direct = bk._total(base, ratio_r=cmap.min_ratio,
                       flash_days=cmap.min_days, site_config=ov.SITE_SINGLE)
    assert cmap.min_cost == pytest.approx(direct)


def test_격자_모든_칸이_개별_계산과_일치(base):
    cmap = bk.cost_map(base, ov.SITE_SINGLE,
                       days_axis=[30, 90, 365], ratio_axis=[5.0, 22.0])
    for i, d in enumerate(cmap.days_axis):
        for j, r in enumerate(cmap.ratio_axis):
            direct = bk._total(base, ratio_r=r, flash_days=d,
                               site_config=ov.SITE_SINGLE)
            assert cmap.grid[i][j] == pytest.approx(direct)


def test_격자_기간이_길수록_비용이_커야_한다(base):
    cmap = bk.cost_map(base, ov.SITE_SINGLE)
    for j in range(len(cmap.ratio_axis)):
        col = [cmap.grid[i][j] for i in range(len(cmap.days_axis))]
        assert col == sorted(col), "즉시검색 기간이 길어지는데 비용이 줄었습니다."


def test_격자_CSV로_저장된다(base, tmp_path):
    cmap = bk.cost_map(base, ov.SITE_SINGLE,
                       days_axis=[30, 90], ratio_axis=[5.0, 22.0])
    path = bk.write_cost_map_csv(cmap, tmp_path / "map.csv")
    text = path.read_text(encoding="utf-8-sig")
    assert "flash_days" in text
    assert "R=22.0" in text
    assert "site_config=single_site" in text


# =============================================================================
# 필수 검증 5 — 경로 분리
# =============================================================================

def test_경로_구성별로_손익분기가_따로_나와야_한다(base):
    """단일 사이트와 다중 사이트의 결과가 같으면 구성이 반영되지 않은 것이다."""
    single = bk.ratio_breakeven(base, ov.SITE_SINGLE)
    multi = bk.ratio_breakeven(base, ov.SITE_MULTI)
    assert single.root != multi.root


def test_경로_단일사이트는_전_구간에서_계층화가_유리(base):
    """탐색 범위 안에서 교차점이 없어야 한다.
    이것이 가설 H1 부분 반증의 근거다.
    """
    rb = bk.ratio_breakeven(base, ov.SITE_SINGLE)
    assert rb.root is None
    assert rb.in_range is False


def test_경로_다중사이트_전환점이_관측범위_밖(base):
    """R = 1.731 은 스캔 범위 3.0~30.0 밖이다."""
    rb = bk.ratio_breakeven(base, ov.SITE_MULTI)
    assert rb.root == pytest.approx(1.731, abs=0.01)
    assert rb.in_range is False


def test_경로_지도가_구성별로_달라야_한다(base):
    s = bk.cost_map(base, ov.SITE_SINGLE, days_axis=[90], ratio_axis=[22.0])
    m = bk.cost_map(base, ov.SITE_MULTI, days_axis=[90], ratio_axis=[22.0])
    assert m.grid[0][0] > s.grid[0][0]


# =============================================================================
# 통합 검증 — 도출된 관계
# =============================================================================

def test_관계_오버헤드_손익분기가_R에_정비례한다(base):
    """오버헤드 손익분기 = R x 3.328 관계가 성립하는지 확인한다.

    3.328 은 Phase 3 에서 찾은 용량 기준 손익분기와 같다. 배수 R 이 정확히
    그만큼 여유를 만들어준다는 뜻이다.
    """
    ratios = []
    for r in (3.0, 10.0, 22.0, 30.0):
        ob = bk.overhead_breakeven(base, r)
        assert ob is not None
        ratios.append(ob / r)
    for x in ratios:
        assert x == pytest.approx(ratios[0], rel=1e-3)
    assert ratios[0] == pytest.approx(3.328, abs=0.01)


def test_관계_오버헤드_탐색_후_상수가_복원된다(base):
    """탐색 중 모듈 상수를 일시 치환하므로 반드시 복원되어야 한다."""
    before = ov.OBJECT_EC_SINGLE
    bk.overhead_breakeven(base, 22.0)
    assert ov.OBJECT_EC_SINGLE == before


def test_관계_기준기간이_짧을수록_임계R이_높아진다(base):
    """역설적 성질. 이미 계층화를 공격적으로 한 조직은 더 줄일 여지가 없어
    오버헤드 증가를 상쇄하기 어렵다.
    """
    prev = None
    for ref in (30, 60, 90, 180, 365):
        r = bk.critical_ratio(base, reference_days=ref).critical_r
        assert r is not None
        if prev is not None:
            assert r < prev, f"기준 {ref}일에서 임계 R이 낮아지지 않았습니다."
        prev = r


def test_관계_현재_시장에서_90일_기준은_수용_가능(base):
    """R = 22 는 90일 기준 임계값 11.30 을 넘는다."""
    crc = bk.critical_ratio(base, reference_days=90)
    assert crc.critical_r < 22.0
    assert crc.in_range is True


def test_관계_30일_기준은_현재_시장에서_수용_불가(base):
    """임계 R = 33.84 로 관측 범위 상한(30.0)을 넘는다."""
    crc = bk.critical_ratio(base, reference_days=30)
    assert crc.critical_r > 30.0
    assert crc.in_range is False
