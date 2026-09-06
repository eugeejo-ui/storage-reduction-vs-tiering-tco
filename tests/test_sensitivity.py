"""
test_sensitivity.py — Phase 6 민감도·몬테카를로 검증

핵심 목적
    1. 명시적 유지 목록에서 항목이 누락되지 않는가 (선행 프로젝트 재발 방지)
    2. 결정론 결과가 분포 안에 들어가는가
    3. 같은 씨앗에서 결과가 재현되는가
    4. 표본이 원장 범위를 벗어나지 않는가
    5. 임계 배수에 무관한 파라미터가 실제로 무관한가
"""

from pathlib import Path

import pytest

import breakeven as bk
import cost_model as cm
import overhead as ov
import sensitivity as sn
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
# 필수 검증 1 — 명시적 유지 목록
# =============================================================================

def test_목록_상각기간이_총비용_대상에_포함되어야_한다():
    """선행 프로젝트에서 상태 격상과 함께 조용히 누락된 사례가 있었다.
    상각기간은 confirmed 등급이라 자동 추출에 잡히지 않는다.
    """
    assert "amortization_years" in sn.declared_targets()["total_cost"]


def test_목록_색인DRR이_임계배수_대상에_포함되어야_한다():
    assert "drr_tsidx" in sn.declared_targets()["critical_ratio"]


def test_목록_복제구성이_임계배수_대상에_포함되어야_한다():
    """Phase 6 에서 새로 발견된 항목. 임계 배수를 7.5 이상 움직인다."""
    assert "replication" in sn.declared_targets()["critical_ratio"]


def test_목록_두_층이_서로_겹치지_않아야_한다():
    """임계 배수를 움직이는 항목과 총비용만 움직이는 항목은 구분된다."""
    d = sn.declared_targets()
    assert set(d["critical_ratio"]) & set(d["total_cost"]) == set()


def test_목록_자동추출만으로는_부족함을_확인(ls):
    """자동 추출 목록에 색인 DRR 과 복제 구성이 없어야 한다.
    있다면 명시적 유지 목록이 불필요하다는 뜻이므로 설계를 재검토해야 한다.
    """
    auto = [k for k, _ in ls.media.sensitivity_targets()]
    assert "drr_tsidx" not in auto
    assert "replication" not in auto


# =============================================================================
# 필수 검증 2 — 결정론 결과가 분포 안에 있는가
# =============================================================================

def test_분포_결정론_결과가_구간_안에_있어야_한다(base):
    mc = sn.monte_carlo_critical_ratio(base, 90, iterations=300)
    assert mc.p05 <= mc.deterministic <= mc.p95


def test_분포_백분위가_순서대로여야_한다(base):
    mc = sn.monte_carlo_critical_ratio(base, 90, iterations=300)
    assert mc.p05 <= mc.p25 <= mc.median <= mc.p75 <= mc.p95


def test_분포_표본_수가_반복_횟수와_일치(base):
    mc = sn.monte_carlo_critical_ratio(base, 90, iterations=200)
    assert mc.n == 200


def test_분포_확률이_0과_1_사이(base):
    mc = sn.monte_carlo_critical_ratio(base, 90, iterations=200)
    p = mc.prob_below(22.0)
    assert 0.0 <= p <= 1.0


# =============================================================================
# 필수 검증 3 — 재현성
# =============================================================================

def test_재현성_같은_씨앗은_같은_결과(base):
    a = sn.monte_carlo_critical_ratio(base, 90, iterations=200, seed=1234)
    b = sn.monte_carlo_critical_ratio(base, 90, iterations=200, seed=1234)
    assert a.median == b.median
    assert a.p05 == b.p05


def test_재현성_다른_씨앗은_다른_결과(base):
    a = sn.monte_carlo_critical_ratio(base, 90, iterations=200, seed=1)
    b = sn.monte_carlo_critical_ratio(base, 90, iterations=200, seed=2)
    assert a.samples != b.samples


# =============================================================================
# 필수 검증 4 — 표본이 원장 범위를 벗어나지 않는가
# =============================================================================

def test_범위_색인DRR_범위가_Phase1과_일치():
    """docs/phase01_reduction.md 5절의 범위와 같아야 한다."""
    assert sn.DRR_TSIDX_RANGE == (1.0, 6.0)


def test_범위_상각기간_후보가_법령_범위와_일치():
    """법인세법 시행규칙 별표 5. 기준 5년, 범위 4~6년."""
    assert sn.AMORTIZATION_CHOICES == (4, 5, 6)


def test_범위_복제구성_후보에_기본값이_포함(base):
    """기본값 1/1/1 은 가장 보수적인 전제이므로 반드시 포함되어야 한다."""
    assert (1, 1, 1) in sn.REPLICATION_CHOICES


def test_범위_복제구성_후보가_전부_유효(base):
    """RF, SF, 사본 수는 모두 1 이상이어야 한다."""
    for rf, sf, ac in sn.REPLICATION_CHOICES:
        assert rf >= 1 and sf >= 1 and ac >= 1


def test_범위_복제_고정시_기본값만_사용된다(base):
    """fix_replication 이면 복제 구성이 흔들리지 않으므로
    분포 폭이 좁아져야 한다.
    """
    free = sn.monte_carlo_critical_ratio(base, 90, iterations=300)
    fixed = sn.monte_carlo_critical_ratio(base, 90, iterations=300,
                                          fix_replication=True)
    assert (fixed.p95 - fixed.p05) < (free.p95 - free.p05)


# =============================================================================
# 필수 검증 5 — 무관 파라미터
# =============================================================================

def test_무관_상각기간은_임계배수를_바꾸지_않는다(base):
    """비교 대상 양쪽에 똑같이 적용되므로 비율이 유지된다.
    이 검증이 실패하면 Phase 5 의 기준선 무관성 성질이 깨진 것이다.
    """
    results = {r.name: r for r in sn.one_way_critical_ratio(base)}
    assert results["상각기간"].affects is False


def test_무관_프리미엄은_임계배수를_바꾸지_않는다(base):
    results = {r.name: r for r in sn.one_way_critical_ratio(base)}
    assert results["정품 프리미엄"].affects is False


def test_무관_기준선과_로그량은_임계배수를_바꾸지_않는다(base):
    results = {r.name: r for r in sn.one_way_critical_ratio(base)}
    assert results["아카이브 기준선 배수"].affects is False
    assert results["일일 로그량"].affects is False


def test_유관_색인DRR과_복제구성은_임계배수를_바꾼다(base):
    results = {r.name: r for r in sn.one_way_critical_ratio(base)}
    assert results["색인 DRR"].affects is True
    assert results["복제 구성 (RF/SF/사본)"].affects is True


def test_유관_상각기간은_총비용을_정확히_2배_바꾼다(base):
    """4년 상각은 5년 계약에서 재구매가 발생한다."""
    results = {r.name: r for r in sn.one_way_total_cost(base)}
    r = results["상각기간"]
    assert r.high / r.low == pytest.approx(2.0)


# =============================================================================
# 통합 검증 — 판정 안정성
# =============================================================================

def test_판정_90일_이상은_안정적이어야_한다(base):
    """결정론 임계값 11.30 과 시장 배수 22 의 여유가 크다."""
    v = sn.verdict_stability(base, 90, iterations=300)
    assert v.deterministic_verdict == "수용 가능"
    assert v.prob_acceptable >= 0.9
    assert v.stable is True


def test_판정_30일은_불안정해야_한다(base):
    """결정론 판정은 '수용 불가'이나 확률이 한쪽으로 쏠리지 않는다.
    Phase 5 의 점 추정만으로 결론을 내면 안 되는 근거다.
    """
    v = sn.verdict_stability(base, 30, iterations=300)
    assert v.deterministic_verdict == "수용 불가"
    assert v.stable is False


def test_판정_기준기간이_길수록_수용확률이_높아진다(base):
    prev = -1.0
    for ref in (30, 60, 90, 180):
        p = sn.verdict_stability(base, ref, iterations=300).prob_acceptable
        assert p >= prev
        prev = p


def test_저장_민감도_CSV가_생성된다(base, tmp_path):
    rs = sn.one_way_critical_ratio(base)
    p = sn.write_sensitivity_csv(rs, tmp_path / "s.csv", "critical_ratio")
    text = p.read_text(encoding="utf-8-sig")
    assert "parameter" in text and "색인 DRR" in text


def test_저장_몬테카를로_CSV가_생성된다(base, tmp_path):
    rs = [sn.monte_carlo_critical_ratio(base, d, iterations=100)
          for d in (90, 180)]
    p = sn.write_monte_carlo_csv(rs, tmp_path / "mc.csv")
    text = p.read_text(encoding="utf-8-sig")
    assert "reference_days" in text and "median" in text
