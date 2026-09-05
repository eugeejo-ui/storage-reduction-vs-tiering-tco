"""
test_placement.py — Phase 3 모듈 검증

이 파일의 목적은 코드가 도는지 확인하는 데 있지 않다.
Phase 0~2 에서 문서로 확정한 판단이 코드에서 지켜지는지 확인하는 데 있다.

특히 다음 두 가지는 선행 프로젝트에서 실제로 발생했던 오류 유형을 막는다.
  - 이중 감축 (선행 프로젝트 오류 1번과 같은 유형: 계수 중복 적용)
  - 기본값에 의한 조용한 가정 (선행 프로젝트 설계 결함: 민감도 목록 누락)
"""

import pytest

import overhead as ov
import placement as pl
import reduction as rd


# =============================================================================
# 필수 검증 1 — 이중 감축 방지
# =============================================================================

def test_이중감축_압축상태에_따라_결과가_달라야_한다():
    """압축 플래그를 켰을 때와 껐을 때 결과가 같으면 실패한다.

    같다면 플래그가 계산에 반영되지 않고 있다는 뜻이며,
    사전 압축 데이터에 어레이 감축을 그대로 적용하는 오류가 발생한다.
    """
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    cfg = ov.single_site()

    uncompressed = rd.apply_reduction(
        p, rd.ReductionPolicy(compression_state=rd.CompressionState.UNCOMPRESSED), cfg
    )
    compressed = rd.apply_reduction(
        p, rd.ReductionPolicy(compression_state=rd.CompressionState.HOST_COMPRESSED), cfg
    )

    assert uncompressed.total_physical_tb != compressed.total_physical_tb, (
        "압축 상태가 물리 용량에 반영되지 않았습니다. 이중 감축 가드가 무력화된 상태입니다."
    )
    # 사전 압축된 쪽이 반드시 더 많은 물리 용량을 요구해야 한다.
    assert compressed.total_physical_tb > uncompressed.total_physical_tb


def test_이중감축_사전압축시_원본_감축률은_1에_가까워야_한다():
    pol = rd.ReductionPolicy(compression_state=rd.CompressionState.HOST_COMPRESSED)
    assert pol.drr_rawdata == 1.00


def test_이중감축_사전압축_데이터에_보증치를_적용하면_예외():
    """제조사 약관이 호스트 압축 데이터를 감축 보증에서 제외한다.

    그 사실을 무시하고 6:1 을 넣으려는 시도를 코드가 막아야 한다.
    """
    with pytest.raises(rd.ReductionError, match="사전 압축"):
        rd.ReductionPolicy(
            compression_state=rd.CompressionState.HOST_COMPRESSED,
            drr_rawdata=rd.DRR_GUARANTEE_GEN3,
        )


# =============================================================================
# 필수 검증 2 — 감축 상한
# =============================================================================

def test_상한_혼합감축률이_절대상한을_초과할_수_없다():
    """원본 감축률이 1.00 이면 색인이 완전히 사라져도 3.333 을 넘을 수 없다."""
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    # 색인 감축률을 극단적으로 크게 넣어도 상한을 넘지 않아야 한다.
    drr = rd.blended_drr(
        p.flash_rawdata_tb, p.flash_tsidx_tb,
        drr_rawdata=1.0, drr_tsidx=1_000_000,
    )
    assert drr < rd.DRR_CEILING + 1e-6
    assert drr == pytest.approx(rd.DRR_CEILING, rel=1e-4)


def test_상한_보증치는_본_워크로드에서_도달_불가능하다():
    """제조사 보증 6:1 이 절대 상한을 넘는다는 사실을 코드로 고정한다."""
    assert rd.DRR_GUARANTEE_GEN3 > rd.DRR_CEILING
    assert rd.DRR_CEILING == pytest.approx(3.3333, abs=1e-4)


def test_상한_기준값에서_혼합감축률은_문서와_일치해야_한다():
    """docs/phase01_reduction.md 4.2절의 도출값과 대조한다.

    원본 1.00, 색인 2.00 이면 혼합 DRR 은 0.50 / (0.15 + 0.175) = 1.5385 이다.
    """
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    drr = rd.blended_drr(p.flash_rawdata_tb, p.flash_tsidx_tb, 1.00, 2.00)
    assert drr == pytest.approx(1.5385, abs=1e-4)


# =============================================================================
# 필수 검증 3 — 아카이브 계층 감축률
# =============================================================================

def test_아카이브_사전압축시_감축률이_1이어야_한다():
    """아카이브 계층은 색인이 제거되어 압축된 원본만 남는다.

    감축이 가장 작동하지 않는 데이터가 계층화가 겨냥하는 바로 그 데이터라는
    Phase 1 의 핵심 발견을 코드로 고정한다.
    """
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    r = rd.apply_reduction(
        p, rd.ReductionPolicy(compression_state=rd.CompressionState.HOST_COMPRESSED),
        ov.single_site(),
    )
    assert r.archive_drr == 1.00


def test_아카이브_감축률은_플래시보다_낮아야_한다():
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    r = rd.apply_reduction(
        p, rd.ReductionPolicy(compression_state=rd.CompressionState.HOST_COMPRESSED),
        ov.single_site(),
    )
    assert r.archive_drr < r.flash_blended_drr


# =============================================================================
# 필수 검증 4 — 플래그 누락 시 예외
# =============================================================================

def test_예외_압축상태를_명시하지_않으면_생성_불가():
    with pytest.raises(TypeError):
        rd.ReductionPolicy()


def test_예외_압축상태에_문자열을_넣으면_예외():
    """열거형이 아닌 값을 넣어 우회하려는 시도를 막는다."""
    with pytest.raises(rd.ReductionError, match="compression_state"):
        rd.ReductionPolicy(compression_state="host_compressed")


def test_예외_정책_없이_감축을_적용하면_예외():
    p = pl.compute_placement(100)
    with pytest.raises((rd.ReductionError, TypeError)):
        rd.apply_reduction(p, None, ov.single_site())


# =============================================================================
# 필수 검증 5 — 오버헤드 구성 조건
# =============================================================================

def test_오버헤드_구성조건_없이_호출하면_예외():
    """단일 사이트와 다중 사이트의 계수가 4배 이상 차이나므로
    기본값을 두지 않는다는 설계를 고정한다.
    """
    with pytest.raises(ov.OverheadError, match="구성 조건"):
        ov.get_overhead(ov.TIER_FLASH, None)


def test_오버헤드_잘못된_사이트구성은_예외():
    with pytest.raises(ov.OverheadError, match="site_config"):
        ov.OverheadConfig(site_config="dual_site")


def test_오버헤드_잘못된_계층명은_예외():
    with pytest.raises(ov.OverheadError, match="tier"):
        ov.get_overhead("cold", ov.single_site())


def test_오버헤드_계수가_문서와_일치해야_한다():
    """Phase 2 에서 확정한 세 계수를 코드로 고정한다."""
    assert ov.BLOCK_RAID == 1.536          # 국내 조달, 7.68TB → 5TB
    assert ov.OBJECT_EC_SINGLE == 1.333    # 제조사 사양서, 12+4
    assert ov.OBJECT_MULTISITE == 5.76     # 국내 조달, 576TB → 100TB


def test_오버헤드_다중사이트가_단일사이트보다_커야_한다():
    single = ov.get_overhead(ov.TIER_ARCHIVE, ov.single_site())
    multi = ov.get_overhead(ov.TIER_ARCHIVE, ov.multi_site())
    assert multi > single
    assert multi / single == pytest.approx(4.32, abs=0.01)


def test_오버헤드_플래시는_사이트구성에_영향받지_않는다():
    """블록 계층은 각 사이트 로컬에 두므로 사이트 수가 계수를 바꾸지 않는다."""
    assert (ov.get_overhead(ov.TIER_FLASH, ov.single_site())
            == ov.get_overhead(ov.TIER_FLASH, ov.multi_site()))


# =============================================================================
# 필수 검증 6 — 배치 계산 검산
# =============================================================================

def test_배치_승계계수가_변경되지_않았다():
    """Splunk 공식 문서에서 확인한 계수를 고정한다."""
    assert pl.RAWDATA_RATIO == 0.15
    assert pl.TSIDX_RATIO == 0.35
    assert pl.ARCHIVE_RATIO == 0.15
    assert pl.RAWDATA_RATIO + pl.TSIDX_RATIO == pytest.approx(0.50)


def test_배치_손계산과_일치해야_한다():
    """100GB/day, 90일 즉시검색, 730일 보관, 사본 1벌.

    플래시   = (0.15 + 0.35) x 100 x 90 / 1000 = 4.5 TB
    아카이브 = 0.15 x 100 x 640 / 1000         = 9.6 TB
    """
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    assert p.flash_logical_tb == pytest.approx(4.5)
    assert p.archive_logical_tb == pytest.approx(9.6)
    assert p.flash_rawdata_tb == pytest.approx(1.35)
    assert p.flash_tsidx_tb == pytest.approx(3.15)


def test_배치_계층화_없으면_아카이브가_0이어야_한다():
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=730))
    assert p.archive_logical_tb == 0.0
    assert p.flash_logical_tb == pytest.approx(36.5)


def test_배치_복제계수를_분리해_적용해야_한다():
    """RF 와 SF 를 하나로 뭉치면 저장량을 잘못 산정한다.
    (선행 프로젝트 오류 2번과 같은 유형)
    """
    lc = pl.LifecyclePolicy(retention_days=730, flash_days=90)
    same = pl.compute_placement(100, lc, pl.ReplicationPolicy(rf=3, sf=3))
    diff = pl.compute_placement(100, lc, pl.ReplicationPolicy(rf=3, sf=2))
    assert same.flash_logical_tb != diff.flash_logical_tb


def test_배치_보관기간을_초과하는_즉시검색기간은_예외():
    with pytest.raises(pl.PlacementError, match="초과"):
        pl.LifecyclePolicy(retention_days=730, flash_days=731)


def test_배치_법정_보관기간_기본값이_2년이어야_한다():
    """개인정보의 안전성 확보조치 기준 제8조. 5만명 이상 시 2년 이상."""
    assert pl.DEFAULT_RETENTION_DAYS == 730


# =============================================================================
# 통합 검증 — 계층화가 물리 용량을 실제로 줄이는가
# =============================================================================

def test_통합_계층화가_진행될수록_물리용량이_줄어야_한다():
    """단조성 확인. 즉시검색 기간을 줄이면 총 물리 용량이 줄어야 한다.

    아카이브 계층이 원본만 보관하므로(0.15 대 0.50) 계층화가 진행될수록
    총량이 감소한다. 이 방향이 뒤집히면 계산 어딘가가 잘못된 것이다.
    """
    cfg = ov.single_site()
    pol_kwargs = dict(compression_state=rd.CompressionState.HOST_COMPRESSED)

    prev = None
    for days in (730, 365, 180, 90, 30, 1):
        p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=days))
        r = rd.apply_reduction(p, rd.ReductionPolicy(**pol_kwargs), cfg)
        if prev is not None:
            assert r.total_physical_tb < prev, (
                f"flash_days={days}에서 물리 용량이 줄지 않았습니다."
            )
        prev = r.total_physical_tb


def test_통합_다중사이트에서는_계층화_이득이_줄어야_한다():
    """오버헤드가 4.32배 커지므로 계층화의 용량 이득이 상쇄된다.

    이 관계가 성립하지 않으면 오버헤드가 계산에 반영되지 않고 있다는 뜻이다.
    """
    pol = rd.ReductionPolicy(compression_state=rd.CompressionState.HOST_COMPRESSED)
    no_tier = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=730))
    tiered = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=30))

    gain_single = (rd.apply_reduction(no_tier, pol, ov.single_site()).total_physical_tb
                   - rd.apply_reduction(tiered, pol, ov.single_site()).total_physical_tb)
    gain_multi = (rd.apply_reduction(no_tier, pol, ov.multi_site()).total_physical_tb
                  - rd.apply_reduction(tiered, pol, ov.multi_site()).total_physical_tb)

    assert gain_single > gain_multi


def test_통합_감축과_계층화는_서로_상쇄해야_한다():
    """가설 H2 검증.

    계층화를 많이 할수록 색인 감축률 개선의 절대 효과가 줄어야 한다.
    아카이브 계층에는 색인이 없어 감축 개선이 적용될 대상이 없기 때문이다.
    """
    cfg = ov.single_site()
    low = dict(compression_state=rd.CompressionState.HOST_COMPRESSED, drr_tsidx=1.0)
    high = dict(compression_state=rd.CompressionState.HOST_COMPRESSED, drr_tsidx=6.0)

    def gain(flash_days):
        p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=flash_days))
        a = rd.apply_reduction(p, rd.ReductionPolicy(**low), cfg).total_physical_tb
        b = rd.apply_reduction(p, rd.ReductionPolicy(**high), cfg).total_physical_tb
        return a - b

    gain_no_tiering = gain(730)
    gain_heavy_tiering = gain(30)

    assert gain_heavy_tiering < gain_no_tiering, (
        "계층화가 진행되어도 감축 개선 효과가 줄지 않았습니다. "
        "두 레버가 독립적으로 계산되고 있다는 뜻입니다."
    )


def test_통합_적용순서가_감축_다음_오버헤드여야_한다():
    """순서를 바꾸면 패리티까지 감축 대상으로 세게 되어 물리 용량이
    과소 산정된다. 실제 산출값이 (논리 / 감축) x 오버헤드 인지 확인한다.
    """
    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    pol = rd.ReductionPolicy(compression_state=rd.CompressionState.HOST_COMPRESSED)
    cfg = ov.single_site()
    r = rd.apply_reduction(p, pol, cfg)

    expected_flash = (p.flash_logical_tb / r.flash_blended_drr) * ov.BLOCK_RAID
    expected_archive = (p.archive_logical_tb / r.archive_drr) * ov.OBJECT_EC_SINGLE

    assert r.flash_physical_tb == pytest.approx(expected_flash, rel=1e-6)
    assert r.archive_physical_tb == pytest.approx(expected_archive, rel=1e-6)
