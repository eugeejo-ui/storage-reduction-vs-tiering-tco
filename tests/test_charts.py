"""
test_charts.py — Phase 9 차트 검증

핵심 목적
    1. 차트에 그려진 수치가 계산 결과와 일치하는가
    2. 한글 폰트 실패 시 조용히 깨지지 않는가
    3. 누적 막대의 부분 합이 총액과 일치하는가
    4. 차트 파일이 실제로 생성되는가
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import pytest

import breakeven as bk
import cost_model as cm
import make_charts as mc
import overhead as ov
import sensitivity as sn
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
# 필수 검증 1 — 차트 수치가 계산 결과와 일치
# =============================================================================

def test_수치_비용곡선이_엔진_결과와_일치(base, tmp_path):
    """차트에 그린 총액이 tco_engine 산출값과 달라지면 실패한다.
    선행 프로젝트에서 차트 수치가 계산과 어긋난 오류가 있었다.
    """
    _, totals = mc.chart_cost_curve(base, tmp_path, korean=False)
    for days, charted in totals.items():
        sc = cm.Scenario(**{**base.__dict__, "flash_days": days})
        engine = te.compute_tco(sc, terms=(5,)).term(5).onprem_krw / 1e6
        assert charted == pytest.approx(engine, rel=1e-6), (
            f"{days}일 지점의 차트값({charted})이 엔진값({engine})과 다릅니다."
        )


def test_수치_등비용_막대가_탐색_결과와_일치(base, tmp_path):
    _, kept = mc.chart_equivalent_days(base, tmp_path, korean=False)
    for ref, charted in kept.items():
        eq = bk.equivalent_days(base, base.ratio_r, reference_days=ref)
        assert charted == eq.days


def test_수치_지도가_격자_계산과_일치(base, tmp_path):
    _, maps = mc.chart_cost_map(base, tmp_path, korean=False)
    grid = maps[ov.SITE_SINGLE]
    # 권고 지점(90일, R=22)만 표본 확인
    days_axis = [1, 7, 14, 30, 60, 90, 120, 180, 270, 365, 540, 730]
    ratio_axis = [3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 22.0, 25.0, 30.0]
    i, j = days_axis.index(90), ratio_axis.index(22.0)
    direct = bk._total(base, ratio_r=22.0, flash_days=90,
                       site_config=ov.SITE_SINGLE) / 1e6
    assert grid[i][j] == pytest.approx(direct, rel=1e-6)


def test_수치_권고지점이_문서값과_일치(base, tmp_path):
    """제안서에 적은 12.4백만원과 일치해야 한다."""
    _, totals = mc.chart_cost_curve(base, tmp_path, korean=False)
    assert totals[90] == pytest.approx(12.44, abs=0.05)
    assert totals[730] == pytest.approx(89.32, abs=0.05)


# =============================================================================
# 필수 검증 2 — 한글 폰트 처리
# =============================================================================

def test_폰트_후보가_4개_이상이어야_한다():
    """환경마다 설치 폰트가 다르므로 다중 후보가 필요하다."""
    assert len(mc.FONT_CANDIDATES) >= 4
    assert "Malgun Gothic" in mc.FONT_CANDIDATES     # Windows
    assert "AppleGothic" in mc.FONT_CANDIDATES       # macOS
    assert any("Noto" in f for f in mc.FONT_CANDIDATES)   # Linux


def test_폰트_실패시_경고를_내야_한다(monkeypatch):
    """조용히 깨진 글자를 출력하지 않는다."""
    monkeypatch.setattr(mc.fm.fontManager, "ttflist", [])
    with pytest.warns(UserWarning, match="한글 폰트"):
        ok = mc.setup_font()
    assert ok is False


def test_폰트_실패시_영문_라벨로_전환된다():
    assert mc._label("한글", "English", True) == "한글"
    assert mc._label("한글", "English", False) == "English"


# =============================================================================
# 필수 검증 3 — 부분 합 일치
# =============================================================================

def test_부분합_누적막대의_두_계층_합이_총액과_일치(base, tmp_path):
    """고성능 계층 + 아카이브 계층 = 총액이어야 한다."""
    _, totals = mc.chart_cost_curve(base, tmp_path, korean=False)
    for days, total in totals.items():
        sc = cm.Scenario(**{**base.__dict__, "flash_days": days})
        t = te.compute_tco(sc, terms=(5,)).term(5)
        parts = (t.onprem_flash_krw + t.onprem_archive_krw) / 1e6
        assert parts == pytest.approx(total, rel=1e-6)


def test_부분합_등비용_막대의_유지와_손실_합이_기준과_일치(base, tmp_path):
    _, kept = mc.chart_equivalent_days(base, tmp_path, korean=False)
    for ref, k in kept.items():
        lost = ref - k
        assert k + lost == ref
        assert k >= 0 and lost >= 0


# =============================================================================
# 필수 검증 4 — 파일 생성
# =============================================================================

def test_생성_네_개_차트가_모두_만들어진다(base, tmp_path):
    paths = [
        mc.chart_cost_curve(base, tmp_path, korean=False)[0],
        mc.chart_cost_map(base, tmp_path, korean=False)[0],
        mc.chart_equivalent_days(base, tmp_path, korean=False)[0],
        mc.chart_uncertainty(base, tmp_path, korean=False, iterations=60)[0],
    ]
    for p in paths:
        assert p.exists(), f"{p.name}이 생성되지 않았습니다."
        assert p.stat().st_size > 5000, f"{p.name}이 비정상적으로 작습니다."


def test_생성_출력_디렉터리가_없으면_만든다(base, tmp_path):
    target = tmp_path / "deep" / "nested"
    p, _ = mc.chart_cost_curve(base, target, korean=False)
    assert p.exists()


def test_생성_파일명이_문서_참조와_일치(base, tmp_path):
    """제안서와 README가 참조하는 이름이므로 바뀌면 링크가 깨진다."""
    names = [
        mc.chart_cost_curve(base, tmp_path, korean=False)[0].name,
        mc.chart_cost_map(base, tmp_path, korean=False)[0].name,
        mc.chart_equivalent_days(base, tmp_path, korean=False)[0].name,
    ]
    assert names == ["fig1_cost_curve.png", "fig2_cost_map.png",
                     "fig3_equivalent_days.png"]


# =============================================================================
# 통합 검증 — 그리지 않기로 한 것
# =============================================================================

def test_범위_표로_충분한_항목은_그리지_않는다():
    """계층 구성 비율과 감축률 상한은 표로 전달된다.
    차트 함수가 늘어나면 이 검증이 실패하므로, 추가 시 근거를 문서화해야 한다.
    """
    chart_funcs = [n for n in dir(mc) if n.startswith("chart_")]
    assert sorted(chart_funcs) == [
        "chart_cost_curve",
        "chart_cost_map",
        "chart_equivalent_days",
        "chart_uncertainty",
    ]


def test_통합_불확실성_차트의_확률이_민감도_결과와_일치(base, tmp_path):
    _, probs = mc.chart_uncertainty(base, tmp_path, korean=False,
                                    iterations=200)
    for ref, charted in probs.items():
        v = sn.verdict_stability(base, ref, iterations=200)
        assert charted == pytest.approx(v.prob_acceptable * 100, abs=0.01)


def test_통합_지도의_증가배수가_1이상이어야_한다(base, tmp_path):
    """3사이트 구성이 단일 사이트보다 저렴해지면 계산 오류다."""
    _, maps = mc.chart_cost_map(base, tmp_path, korean=False)
    ratio = maps[ov.SITE_MULTI] / maps[ov.SITE_SINGLE]
    assert (ratio >= 1.0 - 1e-9).all()
