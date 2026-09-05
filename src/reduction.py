"""
reduction.py — 실효 용량 모듈 (Phase 3)

역할
    placement.py 가 나눈 계층별 논리 용량에 어레이 데이터 감축을 적용하여
    실제로 사야 하는 물리 용량을 산출한다.

이 모듈의 존재 이유는 오류를 막는 데 있다
    선행 프로젝트의 저장 계수(원본 0.15 / 색인 0.35)는 애플리케이션 계층의
    압축을 이미 반영한 값이다. 어레이의 데이터 감축은 블록 계층에서 별도로
    작동한다. 두 계수를 검증 없이 곱하면 같은 압축을 두 번 세게 되고,
    플래시 소요량이 실제보다 작게 산출되어 올플래시 방안이 부당하게 유리해진다.

    이 우려는 추정이 아니라 제조사 계약서로 확인되었다.
    (Dell PowerStore/PowerMax Data Reduction Guarantee, 2026-09-04 확보)

        보증은 Unreducible Data 에 적용되지 않는다. Unreducible Data 의 예에는
        호스트에서 압축된 데이터, 호스트에서 암호화된 데이터, 오디오·이미지·
        PDF·비디오 파일이 포함되며 이에 한정되지 않는다.

    Splunk 가 어레이에 넘기는 원본(rawdata)은 압축된 형태로 저장된다.
    즉 계약상 감축 보증 대상이 아니다.

이중 감축 가드
    compression_state 를 필수 인자로 요구하며 기본값을 두지 않는다.
    호출자가 반드시 명시해야 하고, 누락하면 예외가 발생한다.
    "기본값이 있으면 아무도 생각하지 않는다"는 것이 이 설계의 전제다.

도출된 상한 (docs/phase01_reduction.md 4.2절)
    혼합 감축률 = (원본비율 + 색인비율) / (원본비율/원본DRR + 색인비율/색인DRR)

    원본 DRR 을 1.00 으로 두면 색인 DRR 이 무한대여도
        0.50 / 0.15 = 3.333
    를 넘을 수 없다. 제조사 보증치 6:1 은 본 워크로드에서 물리적으로 도달 불가능하다.

    아카이브 계층은 색인이 제거되어 압축된 원본만 남으므로 감축률이 1.00 으로
    수렴한다. 감축이 가장 작동하지 않는 데이터가 계층화가 겨냥하는 바로 그
    데이터라는 뜻이다.
"""

from dataclasses import dataclass, field
from enum import Enum

import overhead as ov
import placement as pl


class CompressionState(Enum):
    """어레이에 도달하는 시점의 데이터 압축 상태.

    기본값을 두지 않는다. 호출자가 반드시 명시해야 한다.
    """
    # 애플리케이션 계층에서 압축되지 않은 상태로 어레이에 도달한다.
    UNCOMPRESSED = "uncompressed"
    # 애플리케이션 계층에서 이미 압축된 상태로 어레이에 도달한다.
    # 제조사 약관상 Unreducible Data 에 해당한다.
    HOST_COMPRESSED = "host_compressed"


class ReductionError(Exception):
    """감축 조건이 성립하지 않을 때."""


# --- 감축률 파라미터 기본 범위 ------------------------------------------------
# 사전 압축된 원본에 대한 어레이 감축률.
# 도출값이며 실측이 아니다. 압축 결과물은 무작위성이 높아 블록 단위 일치가
# 발생하기 어려우므로 1.00 을 기준으로 두고 상한만 소폭 열어 둔다.
DRR_RAWDATA_COMPRESSED = {"low": 1.00, "base": 1.00, "high": 1.20}

# 색인 파일에 대한 어레이 감축률.
# 색인 내부 구조와 자체 압축 여부에 관한 공식 자료를 확보하지 못했다.
# assumed 등급이며 민감도 1순위다. base 를 범위 하한 쪽에 두어 감축 레버를
# 과대평가하지 않는 방향으로 설정했다.
DRR_TSIDX = {"low": 1.00, "base": 2.00, "high": 6.00}

# 제조사 보증 배율. 계산 기준값이 아니라 대조용으로만 보관한다.
# 본 워크로드에서는 도달 불가능하다.
DRR_GUARANTEE_GEN3 = 6.0
DRR_GUARANTEE_GEN2 = 5.0

# 혼합 감축률의 절대 상한. 원본 DRR 이 1.00 일 때 색인이 완전히 사라져도
# 넘을 수 없는 값이다. 검증에 사용한다.
DRR_CEILING = (pl.RAWDATA_RATIO + pl.TSIDX_RATIO) / pl.RAWDATA_RATIO  # 3.3333...


@dataclass
class ReductionPolicy:
    """감축률 파라미터.

    compression_state 에 기본값이 없다. 이것이 이중 감축 가드의 핵심이다.
    """
    compression_state: CompressionState
    drr_rawdata: float = None
    drr_tsidx: float = None
    which: str = "base"

    def __post_init__(self):
        if not isinstance(self.compression_state, CompressionState):
            raise ReductionError(
                "compression_state를 CompressionState로 명시해야 합니다. "
                "기본값을 두지 않는 것이 이중 감축 방지 설계의 핵심입니다. "
                f"받은 값: {self.compression_state!r}"
            )
        if self.which not in ("low", "base", "high"):
            raise ReductionError("which는 'low'/'base'/'high' 중 하나여야 합니다.")

        if self.drr_tsidx is None:
            self.drr_tsidx = DRR_TSIDX[self.which]

        if self.drr_rawdata is None:
            if self.compression_state is CompressionState.HOST_COMPRESSED:
                # 사전 압축된 데이터는 제조사 약관상 보증 대상이 아니다.
                self.drr_rawdata = DRR_RAWDATA_COMPRESSED[self.which]
            else:
                # 압축되지 않은 데이터라면 색인과 같은 수준의 감축 여지를 가정한다.
                self.drr_rawdata = DRR_TSIDX[self.which]

        for name in ("drr_rawdata", "drr_tsidx"):
            if getattr(self, name) < 1.0:
                raise ReductionError(
                    f"{name}는 1.0 이상이어야 합니다. "
                    "1.0 미만은 감축이 아니라 팽창을 뜻합니다."
                )

        # 사전 압축 데이터에 보증치를 그대로 적용하려는 시도를 차단한다.
        if (self.compression_state is CompressionState.HOST_COMPRESSED
                and self.drr_rawdata > DRR_RAWDATA_COMPRESSED["high"]):
            raise ReductionError(
                f"사전 압축된 데이터의 원본 감축률을 "
                f"{DRR_RAWDATA_COMPRESSED['high']}보다 크게 둘 수 없습니다. "
                "제조사 약관이 호스트 압축 데이터를 감축 보증에서 제외합니다. "
                f"받은 값: {self.drr_rawdata}"
            )


@dataclass
class ReductionResult:
    """계층별 물리 용량(TB)과 적용된 감축률."""
    flash_physical_tb: float
    archive_physical_tb: float
    flash_blended_drr: float
    archive_drr: float
    total_physical_tb: float = field(init=False)

    def __post_init__(self):
        self.total_physical_tb = round(
            self.flash_physical_tb + self.archive_physical_tb, 6
        )


def blended_drr(rawdata_tb: float, tsidx_tb: float,
                drr_rawdata: float, drr_tsidx: float) -> float:
    """원본과 색인이 섞인 계층의 실효 감축률을 구한다.

    감축률은 조화평균 구조를 따른다. 두 값을 산술평균하면 안 된다.
    감축이 되지 않는 부분이 물리 용량을 지배하기 때문이다.

        혼합 DRR = (원본 + 색인) / (원본/원본DRR + 색인/색인DRR)
    """
    if rawdata_tb < 0 or tsidx_tb < 0:
        raise ReductionError("용량은 0 이상이어야 합니다.")
    logical = rawdata_tb + tsidx_tb
    if logical == 0:
        return 1.0
    physical = rawdata_tb / drr_rawdata + tsidx_tb / drr_tsidx
    return logical / physical


def apply_reduction(
    placement: pl.PlacementResult,
    policy: ReductionPolicy,
    overhead_config: ov.OverheadConfig,
) -> ReductionResult:
    """논리 용량에 감축과 오버헤드를 적용해 물리 용량을 산출한다.

    적용 순서
        논리 용량 → 감축 → 오버헤드 → 물리 용량

        감축을 먼저 적용하는 이유는 어레이가 기록 시점에 감축을 수행하고,
        그 결과에 대해 RAID 나 이레저 코딩의 패리티가 생성되기 때문이다.
        순서를 바꾸면 패리티까지 감축 대상으로 세게 되어 물리 용량이
        과소 산정된다.

    Parameters
    ----------
    placement : PlacementResult
        placement.compute_placement() 의 출력.
    policy : ReductionPolicy
        감축률 파라미터. compression_state 를 반드시 명시해야 한다.
    overhead_config : OverheadConfig
        오버헤드 구성 조건. 생략할 수 없다.

    Returns
    -------
    ReductionResult
    """
    if policy is None:
        raise ReductionError(
            "ReductionPolicy를 명시해야 합니다. compression_state에 "
            "기본값을 두지 않는 것이 이중 감축 방지 설계의 핵심입니다."
        )
    if overhead_config is None:
        raise ReductionError("OverheadConfig를 명시해야 합니다.")

    # 플래시 계층: 원본과 색인이 섞여 있다.
    f_drr = blended_drr(
        placement.flash_rawdata_tb,
        placement.flash_tsidx_tb,
        policy.drr_rawdata,
        policy.drr_tsidx,
    )

    # 아카이브 계층: 색인이 제거되어 원본만 남는다.
    # 사전 압축 상태라면 감축률이 1.00 으로 수렴한다.
    a_drr = policy.drr_rawdata

    f_over = ov.get_overhead(ov.TIER_FLASH, overhead_config)
    a_over = ov.get_overhead(ov.TIER_ARCHIVE, overhead_config)

    flash_physical = placement.flash_logical_tb / f_drr * f_over
    archive_physical = placement.archive_logical_tb / a_drr * a_over

    return ReductionResult(
        flash_physical_tb=round(flash_physical, 6),
        archive_physical_tb=round(archive_physical, 6),
        flash_blended_drr=round(f_drr, 6),
        archive_drr=round(a_drr, 6),
    )


if __name__ == "__main__":
    print("[reduction.py] 검산")
    print(f"  혼합 감축률 절대 상한 : {DRR_CEILING:.4f}")
    print(f"  제조사 보증 최고 배율 : {DRR_GUARANTEE_GEN3}")
    print("  → 보증치는 본 워크로드에서 물리적으로 도달 불가능하다.")
    print()

    p = pl.compute_placement(100, pl.LifecyclePolicy(retention_days=730, flash_days=90))
    cfg = ov.single_site()

    print("100GB/day, 730일 보관, 90일 즉시검색, 단일 사이트")
    print()
    for state in (CompressionState.UNCOMPRESSED, CompressionState.HOST_COMPRESSED):
        pol = ReductionPolicy(compression_state=state)
        r = apply_reduction(p, pol, cfg)
        print(f"  [{state.value}]")
        print(f"    원본 DRR       : {pol.drr_rawdata}")
        print(f"    색인 DRR       : {pol.drr_tsidx}")
        print(f"    플래시 혼합 DRR: {r.flash_blended_drr}")
        print(f"    아카이브 DRR   : {r.archive_drr}")
        print(f"    플래시 물리    : {r.flash_physical_tb} TB")
        print(f"    아카이브 물리  : {r.archive_physical_tb} TB")
        print(f"    합계           : {r.total_physical_tb} TB")
        print()

    print("※ 두 상태의 결과가 다르다는 것이 이중 감축 가드가 작동한다는 증거다.")
