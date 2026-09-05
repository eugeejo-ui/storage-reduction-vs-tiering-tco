"""
placement.py — 계층 배치 모듈 (Phase 3)

역할
    일일 로그량(GB/day)을 계층별 논리 용량(TB)으로 나눈다.
    오버헤드와 감축은 다루지 않는다. 그 둘은 overhead.py 와 reduction.py 가 맡는다.

    이 모듈이 답하는 질문은 하나다. "어느 데이터를 어느 계층에 둘 것인가."

승계한 계수 (선행 프로젝트 phase03b, Splunk 공식 문서 2026-09-02 확인)
    RAWDATA_RATIO = 0.15   원본(rawdata) 비율. 복제 시 RF 가 곱해진다.
    TSIDX_RATIO   = 0.35   색인(tsidx) 비율.   복제 시 SF 가 곱해진다.
    검색 가능 계층 합계 = 0.50
    아카이브 계층 = 0.15 (색인 제거, 원본만)

계층화 축의 정의 (Phase 3 판단 ⑤)
    본 프로젝트는 계층화 비율을 백분율이 아니라 "즉시 검색 가능 기간(일수)"으로
    정의한다. 관리자가 실제로 조정하는 것은 "몇 일 뒤 아카이브로 보낼 것인가"이지
    "몇 퍼센트를 옮길 것인가"가 아니기 때문이다.

    이 방식을 택하면 Phase 1 의 발견이 자동으로 반영된다. 아카이브 계층은
    색인이 제거되어 감축률이 1.00 으로 수렴하므로, 경계를 옮기면 감축 대상
    데이터가 줄어드는 효과가 계산에 저절로 들어온다. 백분율 방식으로 하면
    이 연동을 별도로 구현해야 한다.

    scan_days = retention_days  →  계층화 없음 (전량 플래시)
    scan_days = 1               →  최대 계층화

두 개의 계층만 둔다
    선행 프로젝트는 hot/warm, cold, frozen 의 3계층이었다. 본 프로젝트는
    매체를 기준으로 나누므로 2계층으로 단순화한다.
      flash   : 검색 가능 상태(원본 + 색인). hot/warm 과 cold 를 합친 것.
      archive : 색인 제거 후 원본만. frozen 에 해당.
    3계층으로 늘리면 파라미터가 급증하고, cold 를 어느 매체에 둘지가
    또 하나의 판단 사항이 된다. 필요하면 후속 과제로 분리한다.
"""

from dataclasses import dataclass, field

# --- 승계 계수 (변경 금지, 변경 시 출처 재확인 필요) --------------------------
# Splunk 공식 문서, 선행 프로젝트에서 2026-09-02 확인
RAWDATA_RATIO = 0.15
TSIDX_RATIO = 0.35
ARCHIVE_RATIO = 0.15  # = RAWDATA_RATIO. 아카이브는 원본만 남긴다.

GB_PER_TB = 1000

# 법정 보관 의무 (개인정보의 안전성 확보조치 기준 제8조)
# 5만명 이상 정보주체 처리 시 2년 이상
DEFAULT_RETENTION_DAYS = 730


class PlacementError(Exception):
    """배치 조건이 성립하지 않을 때."""


@dataclass
class LifecyclePolicy:
    """수명주기 정책.

    flash_days 가 본 프로젝트의 스캔 축이다. 기본값을 두되, 손익분기 탐색에서는
    1 부터 retention_days 까지 전 구간을 훑는다.

    법정 최소 즉시조회 기간은 존재하지 않는다. 2025년 10월 31일 개정으로
    점검 주기·방법·사후조치를 개인정보처리자가 자율적으로 정하도록 바뀌었기
    때문이다. 따라서 flash_days 의 상한을 코드가 강제하지 않는다.
    (docs/phase03_placement.md 판단 ②)
    """
    retention_days: int = DEFAULT_RETENTION_DAYS
    flash_days: int = 90

    def __post_init__(self):
        if self.retention_days <= 0:
            raise PlacementError("retention_days는 1 이상이어야 합니다.")
        if self.flash_days < 0:
            raise PlacementError("flash_days는 0 이상이어야 합니다.")
        if self.flash_days > self.retention_days:
            raise PlacementError(
                f"flash_days({self.flash_days})가 "
                f"retention_days({self.retention_days})를 초과할 수 없습니다."
            )

    @property
    def archive_days(self) -> int:
        """보관 기간에서 플래시 구간을 뺀 나머지."""
        return self.retention_days - self.flash_days

    @property
    def tiered_day_ratio(self) -> float:
        """기간 기준 계층화 비율. 결과 표시용이며 계산에는 쓰지 않는다."""
        return self.archive_days / self.retention_days


@dataclass
class ReplicationPolicy:
    """복제 정책. 선행 프로젝트의 정의를 그대로 승계한다.

    RF 와 SF 를 분리하는 이유는 원본과 색인의 복제 계수를 다르게 두는 구성이
    실무에서 흔하기 때문이다. 하나로 뭉치면 저장량을 잘못 산정한다.
    (선행 프로젝트 오류 2번)
    """
    rf: int = 1              # rawdata 복제 수
    sf: int = 1              # tsidx 복제 수
    archive_copies: int = 1  # 아카이브 사본 수

    def __post_init__(self):
        for name in ("rf", "sf", "archive_copies"):
            if getattr(self, name) < 1:
                raise PlacementError(f"{name}은 1 이상이어야 합니다.")


@dataclass
class PlacementResult:
    """계층별 논리 용량(TB).

    '논리'라는 표기가 중요하다. 오버헤드와 감축이 아직 적용되지 않은 값이며,
    이 값을 그대로 비용 계산에 넣으면 안 된다.
    """
    flash_logical_tb: float
    archive_logical_tb: float
    # 감축 계산에 필요하므로 원본과 색인을 분리해 함께 돌려준다.
    flash_rawdata_tb: float
    flash_tsidx_tb: float
    total_logical_tb: float = field(init=False)

    def __post_init__(self):
        self.total_logical_tb = round(
            self.flash_logical_tb + self.archive_logical_tb, 6
        )


def compute_placement(
    daily_gb: float,
    lifecycle: LifecyclePolicy = None,
    replication: ReplicationPolicy = None,
) -> PlacementResult:
    """일일 로그량을 계층별 논리 용량으로 나눈다.

    Parameters
    ----------
    daily_gb : float
        하루 인덱싱 로그량(GB).
    lifecycle : LifecyclePolicy
        수명주기 정책. 생략 시 기본(730일 보관, 90일 즉시검색).
    replication : ReplicationPolicy
        복제 정책. 생략 시 사본 1벌.

    Returns
    -------
    PlacementResult
        오버헤드와 감축이 적용되지 않은 논리 용량.
    """
    if daily_gb < 0:
        raise PlacementError("daily_gb는 0 이상이어야 합니다.")

    lc = lifecycle if lifecycle is not None else LifecyclePolicy()
    rep = replication if replication is not None else ReplicationPolicy()

    # 플래시 계층: 원본과 색인을 분리해 계산한다.
    # 분리하는 이유는 감축률이 서로 다르기 때문이다.
    # 원본은 이미 압축되어 있어 어레이 감축이 거의 작동하지 않는 반면,
    # 색인은 감축 여지가 남아 있을 수 있다. (docs/phase01_reduction.md 4절)
    raw_gb = RAWDATA_RATIO * rep.rf * daily_gb * lc.flash_days
    tsidx_gb = TSIDX_RATIO * rep.sf * daily_gb * lc.flash_days

    # 아카이브 계층: 색인이 제거되어 원본만 남는다.
    archive_gb = ARCHIVE_RATIO * rep.archive_copies * daily_gb * lc.archive_days

    return PlacementResult(
        flash_logical_tb=round((raw_gb + tsidx_gb) / GB_PER_TB, 6),
        archive_logical_tb=round(archive_gb / GB_PER_TB, 6),
        flash_rawdata_tb=round(raw_gb / GB_PER_TB, 6),
        flash_tsidx_tb=round(tsidx_gb / GB_PER_TB, 6),
    )


if __name__ == "__main__":
    print("[placement.py] 검산")
    print()

    # 검산 1 — 선행 프로젝트 예시와 대조
    # 100GB/day, 사본 1벌, 90일 즉시검색, 730일 보관
    res = compute_placement(100, LifecyclePolicy(retention_days=730, flash_days=90))
    print("100GB/day, 730일 보관, 90일 즉시검색, 사본 1벌")
    print(f"  플래시 논리   : {res.flash_logical_tb} TB  (기대 4.5)")
    print(f"    - 원본      : {res.flash_rawdata_tb} TB  (기대 1.35)")
    print(f"    - 색인      : {res.flash_tsidx_tb} TB  (기대 3.15)")
    print(f"  아카이브 논리 : {res.archive_logical_tb} TB  (기대 9.6)")
    print(f"  합계          : {res.total_logical_tb} TB")
    print()

    # 검산 2 — 계층화 없음 (전량 플래시)
    res_none = compute_placement(100, LifecyclePolicy(retention_days=730, flash_days=730))
    print("계층화 없음 (flash_days = 730)")
    print(f"  플래시 논리   : {res_none.flash_logical_tb} TB  (기대 36.5)")
    print(f"  아카이브 논리 : {res_none.archive_logical_tb} TB  (기대 0.0)")
    print()

    # 검산 3 — 최대 계층화
    res_max = compute_placement(100, LifecyclePolicy(retention_days=730, flash_days=1))
    print("최대 계층화 (flash_days = 1)")
    print(f"  플래시 논리   : {res_max.flash_logical_tb} TB  (기대 0.05)")
    print(f"  아카이브 논리 : {res_max.archive_logical_tb} TB  (기대 10.935)")
    print()
    print("※ 이 값들은 논리 용량이다. 오버헤드와 감축을 적용하기 전이므로")
    print("   그대로 비용 계산에 넣으면 안 된다.")
