"""
overhead.py — 용량 오버헤드 계수 모듈 (Phase 3)

역할
    논리 용량을 물리 용량으로 바꿀 때 곱해지는 오버헤드 배수를 결정한다.
    사본과 패리티 때문에 실제로는 논리 용량보다 더 많은 물리 용량이 필요하다.

왜 별도 모듈로 분리했는가 (docs/phase02_media_pricing.md 6절)
    Phase 1에서는 이레저 코딩 오버헤드를 12+4 구성 기준 1.333배 단일값으로
    확정했다. 그러나 Phase 2에서 국내 조달 규격서를 확인한 결과, 실제 값이
    구성 조건에 따라 1.333배에서 5.76배까지 갈렸다.

    단일 계수로 두면 어떤 구성을 전제하느냐가 계층화의 실질 용량 효율을
    4배 이상 바꾸는데도 그 사실이 코드에 드러나지 않는다. 따라서 계수를
    상수로 박지 않고 구성 조건을 받아 선택하는 함수로 분리한다.

확정된 계수 (전부 confirmed 등급)
    BLOCK_RAID          1.536  국내 조달 규격서. 원시 7.68TB → 가용 5TB 이상
    OBJECT_EC_SINGLE    1.333  제조사 사양서. 12+4 이레저 코딩, (12+4)/12
    OBJECT_MULTISITE    5.76   국내 조달 규격서. 원시 576TB → 가용 100TB
                               3개 데이터센터 분산 요구가 얹힌 값

기본 구성 판단 (Phase 3 판단 ④)
    선행 프로젝트의 조직 프로파일에 재해복구·다중 사이트 요건이 명시되어
    있지 않다. 고가용성은 오브젝트 원격 저장소가 원본 역할을 하는 방식으로
    처리되며 단일 사이트를 전제한다. 따라서 기본 구성은 단일 사이트로 두고,
    다중 사이트는 선택지로만 제공한다.

    5.76배를 기본값으로 두면 계층화가 부당하게 불리해진다. 근거가 국내 조달
    사례 1건이며 3개 데이터센터 분산이라는 특수 요건에서 나온 값이기 때문이다.

계층별로 분리하는 이유
    블록 계층과 오브젝트 계층은 물리적으로 다른 방식으로 사본을 만든다.
    블록은 RAID, 오브젝트는 이레저 코딩이다. 하나의 계수로 뭉치면 계산
    체계가 다른 것을 섞는 셈이 된다. (작업원칙 10항)
"""

from dataclasses import dataclass


# --- 확정 계수 (변경 시 출처 재확인 필요) ------------------------------------
# 국내 조달 규격서, 구미시 공고 R26BK01589308-000, 2026-09-04 확인
# 원시 7.68TB (1.92TB x 4EA) → 가용 5TB 이상. 7.68 / 5 = 1.536
BLOCK_RAID = 1.536

# Dell ObjectScale 기술 사양서, 2026-09-04 확인
# 12+4 이레저 코딩 기준 5노드 최소 클러스터. (12 + 4) / 12 = 1.3333...
OBJECT_EC_SINGLE = 1.333

# 국내 조달 규격서, 중진공 공고 R26BK01624183-000, 2026-09-04 확인
# 원시 576TB (3노드 x 12디스크 x 16TB) → 가용 100TB 이상. 576 / 100 = 5.76
# 이레저 코딩 단독이 아니라 3개 데이터센터 분산 요구가 얹힌 값이다.
OBJECT_MULTISITE = 5.76


# 계층 식별자
TIER_FLASH = "flash"
TIER_ARCHIVE = "archive"
VALID_TIERS = (TIER_FLASH, TIER_ARCHIVE)

# 아카이브 계층 배치 구성
SITE_SINGLE = "single_site"
SITE_MULTI = "multi_site"
VALID_SITE_CONFIGS = (SITE_SINGLE, SITE_MULTI)


class OverheadError(Exception):
    """오버헤드 계수를 결정할 조건이 갖춰지지 않았을 때."""


@dataclass(frozen=True)
class OverheadConfig:
    """오버헤드 계수를 결정하는 구성 조건.

    site_config 에 기본값을 두지 않는 것이 이 클래스의 요점이다.
    호출자가 단일 사이트인지 다중 사이트인지 반드시 명시하게 하여,
    4배 이상 차이나는 값이 기본값으로 조용히 들어가는 것을 막는다.
    """
    site_config: str

    def __post_init__(self):
        if self.site_config not in VALID_SITE_CONFIGS:
            raise OverheadError(
                f"site_config는 {VALID_SITE_CONFIGS} 중 하나여야 합니다. "
                f"받은 값: {self.site_config!r}"
            )


def single_site() -> OverheadConfig:
    """단일 사이트 구성. 본 프로젝트의 기준 시나리오."""
    return OverheadConfig(site_config=SITE_SINGLE)


def multi_site() -> OverheadConfig:
    """3개 데이터센터 분산 구성. 선택지이며 기본값이 아니다."""
    return OverheadConfig(site_config=SITE_MULTI)


def get_overhead(tier: str, config: OverheadConfig) -> float:
    """계층과 구성 조건에 맞는 오버헤드 배수를 돌려준다.

    Parameters
    ----------
    tier : str
        'flash' 또는 'archive'.
    config : OverheadConfig
        구성 조건. 생략할 수 없다. 기본값을 두지 않는 것이 설계 의도다.

    Returns
    -------
    float
        논리 용량에 곱할 배수.

    Raises
    ------
    OverheadError
        계층명이 잘못되었거나 config 가 주어지지 않은 경우.
    """
    if config is None:
        raise OverheadError(
            "구성 조건(OverheadConfig)을 명시해야 합니다. "
            "단일 사이트와 다중 사이트의 계수가 4배 이상 차이나므로 "
            "기본값을 두지 않습니다."
        )
    if not isinstance(config, OverheadConfig):
        raise OverheadError(
            f"config는 OverheadConfig 여야 합니다. 받은 타입: {type(config).__name__}"
        )
    if tier not in VALID_TIERS:
        raise OverheadError(
            f"tier는 {VALID_TIERS} 중 하나여야 합니다. 받은 값: {tier!r}"
        )

    if tier == TIER_FLASH:
        # 블록 계층은 사이트 구성과 무관하게 RAID 오버헤드를 따른다.
        # 다중 사이트 구성이더라도 플래시 캐시는 각 사이트 로컬에 두므로
        # 사이트 수가 블록 계층의 오버헤드를 바꾸지 않는다.
        return BLOCK_RAID

    # tier == TIER_ARCHIVE
    if config.site_config == SITE_SINGLE:
        return OBJECT_EC_SINGLE
    return OBJECT_MULTISITE


def describe(config: OverheadConfig) -> dict:
    """현재 구성에서 적용되는 계수를 사람이 읽을 수 있게 돌려준다.

    문서화와 검산에 사용한다.
    """
    return {
        "site_config": config.site_config,
        "flash": get_overhead(TIER_FLASH, config),
        "archive": get_overhead(TIER_ARCHIVE, config),
    }


if __name__ == "__main__":
    print("[overhead.py] 확정 계수")
    print(f"  BLOCK_RAID       : {BLOCK_RAID}  (국내 조달 규격서, 7.68TB → 5TB)")
    print(f"  OBJECT_EC_SINGLE : {OBJECT_EC_SINGLE}  (제조사 사양서, 12+4)")
    print(f"  OBJECT_MULTISITE : {OBJECT_MULTISITE}  (국내 조달 규격서, 576TB → 100TB)")
    print()
    print("[구성별 적용값]")
    for cfg in (single_site(), multi_site()):
        d = describe(cfg)
        print(f"  {d['site_config']:12s} flash={d['flash']}  archive={d['archive']}")
    print()
    print("※ 기본 구성은 단일 사이트다. 다중 사이트를 기본으로 두면")
    print("   계층화가 부당하게 불리해진다. (docs/phase03_placement.md 판단 ④)")
