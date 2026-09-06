"""
make_charts.py — 차트 생성 모듈 (Phase 9)

역할
    제안서에 넣을 차트를 생성한다. 표로 전달되지 않는 것만 그린다.

무엇을 그리지 않는가
    계층 구성 비율(0.50 대 0.15)과 감축률 상한(3.33)은 표로 충분히 전달된다.
    표를 그림으로 바꾸기만 하면 문서만 길어지므로 그리지 않는다.

무엇을 그리는가
    1. 비용 곡선        — 곡선의 꺾임은 표에서 보이지 않는다
    2. 2차원 비용 지도  — 격자는 표로 표현할 수 없다
    3. 등비용 기간 막대 — 격차가 눈에 들어오지 않는다
    4. 임계 배수 상자그림 — 분포 폭은 표로 어렵다

    4번이 특히 중요하다. 30일 기준의 임계값 33.84 는 시장 배수 22 를 54%
    웃돌아 판정이 확실해 보인다. 그러나 임계값 자체가 10.80 에서 50.14 사이에서
    흔들리고, 중앙값 19.32 는 시장 배수보다 낮아 점 추정과 반대 방향을 가리킨다.
    두 숫자를 나란히 놓은 표로는 이 관계가 보이지 않는다.

한글 폰트
    matplotlib 은 기본 폰트로 한글을 렌더링하지 못한다. 환경마다 설치된
    폰트가 다르므로 다중 후보를 순회한다. 전부 실패하면 경고를 출력하고
    영문 라벨로 전환한다. 조용히 깨진 글자를 출력하지 않는다.
"""

import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np

import breakeven as bk
import cost_model as cm
import overhead as ov
import sensitivity as sn
from pricing_loader import load

# --- 한글 폰트 후보 (순회 순서) ----------------------------------------------
FONT_CANDIDATES = (
    "Malgun Gothic",       # Windows
    "AppleGothic",         # macOS
    "Noto Sans CJK KR",    # Linux 배포판
    "Noto Sans CJK JP",    # 한글 글리프 포함
    "NanumGothic",         # 나눔고딕
    "Noto Sans KR",
)

# 색상 — 흑백 인쇄에서도 구분되도록 명도 차이를 둔다
C_FLASH = "#2b4c7e"
C_ARCHIVE = "#8fa8c8"
C_SINGLE = "#2b4c7e"
C_MULTI = "#c1666b"
C_MARK = "#d64545"
C_GRID = "#dddddd"


class ChartError(Exception):
    """차트를 생성할 수 없을 때."""


def setup_font() -> bool:
    """사용 가능한 첫 번째 한글 폰트를 선택한다.

    Returns
    -------
    bool
        한글 렌더링이 가능하면 True. 전부 실패하면 False 를 돌려주고,
        호출부는 영문 라벨로 전환한다.
    """
    available = {f.name for f in fm.fontManager.ttflist}
    for name in FONT_CANDIDATES:
        if name in available:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            print(f"[폰트] '{name}' 선택")
            return True
    warnings.warn(
        "한글 폰트를 찾지 못했습니다. 영문 라벨로 전환합니다. "
        f"확인한 후보: {', '.join(FONT_CANDIDATES)}"
    )
    plt.rcParams["axes.unicode_minus"] = False
    return False


def _label(ko: str, en: str, korean: bool) -> str:
    return ko if korean else en


def _ensure(out_dir) -> Path:
    """저장 직전에 출력 디렉터리를 보장한다.

    main() 에서만 생성하면 개별 차트 함수를 직접 호출할 때 실패한다.
    """
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _style(ax):
    ax.grid(True, color=C_GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


# =============================================================================
# 차트 1 — 즉시검색 기간별 비용 곡선
# =============================================================================

def chart_cost_curve(base, out_dir, korean=True):
    """90일 권고의 근거. 곡선이 어디서 꺾이는지 보여준다."""
    days = [1, 7, 14, 30, 60, 90, 120, 180, 270, 365, 540, 730]
    flash, archive = [], []
    for d in days:
        sc = cm.Scenario(**{**base.__dict__, "flash_days": d})
        out = cm.evaluate(sc)
        ctx, phys = out["price"], out["physical"]
        flash.append(phys.flash_physical_tb * ctx.flash_krw_per_tb / 1e6)
        archive.append(phys.archive_physical_tb
                       * ctx.archive_effective_krw_per_tb / 1e6)

    total = [f + a for f, a in zip(flash, archive)]

    fig, ax = plt.subplots(figsize=(9, 5.2))
    x = np.arange(len(days))
    ax.bar(x, flash, color=C_FLASH, zorder=3,
           label=_label("고성능 계층", "High-performance tier", korean))
    ax.bar(x, archive, bottom=flash, color=C_ARCHIVE, zorder=3,
           label=_label("아카이브 계층", "Archive tier", korean))

    # 권고 지점 표시
    i90 = days.index(90)
    ax.annotate(
        _label(f"권고 90일\n{total[i90]:.1f}백만원",
               f"Recommended: 90d\n{total[i90]:.1f}M KRW", korean),
        xy=(i90, total[i90]), xytext=(i90 + 1.4, total[i90] + 26),
        color=C_MARK, fontsize=10, fontweight="bold",
        arrowprops=dict(arrowstyle="->", color=C_MARK, linewidth=1.4),
    )

    ax.set_xticks(x)
    ax.set_xticklabels([str(d) for d in days])
    ax.set_xlabel(_label("즉시 검색 가능 기간 (일)",
                         "Immediately searchable period (days)", korean))
    ax.set_ylabel(_label("5년 저장 매체 원가 (백만원)",
                         "5-year media cost (M KRW)", korean))
    ax.set_title(_label("즉시 검색 기간에 따른 저장 매체 원가",
                        "Storage media cost by searchable period", korean),
                 fontsize=13, fontweight="bold", pad=14)
    ax.legend(frameon=False)
    _style(ax)

    note = _label(
        "일일 100GB · 730일 보관 · 단일 사이트 · 단가 격차 22배 기준",
        "100GB/day, 730-day retention, single site, 22x price ratio", korean)
    fig.text(0.5, 0.015, note, ha="center", fontsize=8.5, color="#666666")

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path = _ensure(out_dir) / "fig1_cost_curve.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path, dict(zip(days, total))


# =============================================================================
# 차트 2 — 2차원 비용 지도 (단일 / 다중 사이트)
# =============================================================================

def chart_cost_map(base, out_dir, korean=True):
    """격자는 표로 표현할 수 없다. 두 구성을 나란히 놓아 지형을 비교한다."""
    days_axis = [1, 7, 14, 30, 60, 90, 120, 180, 270, 365, 540, 730]
    ratio_axis = [3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 22.0, 25.0, 30.0]

    maps = {}
    for site in (ov.SITE_SINGLE, ov.SITE_MULTI):
        cmap = bk.cost_map(base, site, days_axis, ratio_axis)
        maps[site] = np.array(cmap.grid) / 1e6

    vmax = max(m.max() for m in maps.values())

    # 두 지도만 나란히 놓으면 거의 같아 보인다. 절대값이 큰 구간(장기 보관)은
    # 두 구성이 동일하고, 차이가 나는 구간(단기)은 절대값이 작아 색이 옅기
    # 때문이다. 따라서 증가 배수를 세 번째 패널로 함께 그린다.
    ratio_map = maps[ov.SITE_MULTI] / maps[ov.SITE_SINGLE]

    fig, axes = plt.subplots(1, 3, figsize=(17.5, 5.4))
    fig.subplots_adjust(wspace=0.35)
    titles = {
        ov.SITE_SINGLE: _label("단일 사이트", "Single site", korean),
        ov.SITE_MULTI: _label("3개 사이트 분산", "3-site distributed", korean),
    }

    for ax, site in zip(axes[:2], (ov.SITE_SINGLE, ov.SITE_MULTI)):
        grid = maps[site]
        im = ax.imshow(grid, aspect="auto", origin="lower",
                       cmap="YlOrRd", vmin=0, vmax=vmax)
        ax.set_xticks(range(len(ratio_axis)))
        ax.set_xticklabels([f"{r:g}" for r in ratio_axis])
        ax.set_yticks(range(len(days_axis)))
        ax.set_yticklabels([str(d) for d in days_axis])
        ax.set_xlabel(_label("단가 격차 (배)", "Price ratio", korean))
        ax.set_title(titles[site], fontsize=12, fontweight="bold")
        ax.axvline(ratio_axis.index(22.0), color="#333333",
                   linestyle="--", linewidth=1.2)
        ax.axhline(days_axis.index(90), color=C_MARK,
                   linestyle="--", linewidth=1.2)
        ax.grid(False)

    cbar = fig.colorbar(im, ax=axes[:2], fraction=0.028, pad=0.03)
    cbar.set_label(_label("5년 저장 매체 원가 (백만원)",
                          "5-year media cost (M KRW)", korean))

    # 세 번째 패널 — 증가 배수
    ax3 = axes[2]
    im3 = ax3.imshow(ratio_map, aspect="auto", origin="lower",
                     cmap="Blues", vmin=1.0, vmax=ratio_map.max())
    ax3.set_xticks(range(len(ratio_axis)))
    ax3.set_xticklabels([f"{r:g}" for r in ratio_axis])
    # 왼쪽 패널과 축이 같으므로 눈금 라벨을 생략한다.
    # 남겨두면 왼쪽 컬러바 라벨과 겹친다.
    ax3.set_yticks(range(len(days_axis)))
    ax3.set_yticklabels([])
    ax3.set_xlabel(_label("단가 격차 (배)", "Price ratio", korean))
    ax3.set_title(_label("3사이트 도입 시 증가 배수",
                         "Cost multiplier under 3 sites", korean),
                  fontsize=12, fontweight="bold")
    ax3.axvline(ratio_axis.index(22.0), color="#333333",
                linestyle="--", linewidth=1.2)
    ax3.axhline(days_axis.index(90), color=C_MARK,
                linestyle="--", linewidth=1.2)
    ax3.grid(False)
    cbar3 = fig.colorbar(im3, ax=ax3, fraction=0.046, pad=0.03)
    cbar3.set_label(_label("증가 배수", "Multiplier", korean))

    axes[0].set_ylabel(_label("즉시 검색 기간 (일)",
                              "Searchable period (days)", korean))

    fig.suptitle(_label(
        "저장 매체 원가 지도 — 점선 교차점이 현재 시장·권고 설계",
        "Media cost map — dashed lines mark current market and recommendation",
        korean), fontsize=13, fontweight="bold")

    path = _ensure(out_dir) / "fig2_cost_map.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path, maps


# =============================================================================
# 차트 3 — 등비용 기간 비교
# =============================================================================

def chart_equivalent_days(base, out_dir, korean=True):
    """3사이트 도입의 대가를 일수로 보여준다."""
    refs = [365, 180, 90, 60, 30]
    kept = []
    for r in refs:
        eq = bk.equivalent_days(base, base.ratio_r, reference_days=r)
        kept.append(eq.days)
    lost = [r - k for r, k in zip(refs, kept)]

    fig, ax = plt.subplots(figsize=(9, 4.8))
    y = np.arange(len(refs))
    ax.barh(y, kept, color=C_SINGLE, zorder=3,
            label=_label("유지 가능", "Retained", korean))
    ax.barh(y, lost, left=kept, color=C_MULTI, zorder=3,
            label=_label("잃는 기간", "Lost", korean))

    for i, (k, r) in enumerate(zip(kept, refs)):
        txt = _label(f"{k}일", f"{k}d", korean)
        if k == 0:
            txt = _label("불가능", "Not feasible", korean)
        ax.text(r + 8, i, txt, va="center", fontsize=10,
                fontweight="bold", color=C_MARK if k == 0 else "#333333")

    ax.set_yticks(y)
    ax.set_yticklabels([_label(f"현재 {r}일", f"{r}d today", korean)
                        for r in refs])
    ax.set_xlabel(_label("즉시 검색 가능 기간 (일)",
                         "Immediately searchable period (days)", korean))
    ax.set_title(_label(
        "3개 사이트 분산 도입 시 같은 예산으로 유지 가능한 기간",
        "Retainable period under 3-site distribution at equal budget", korean),
        fontsize=13, fontweight="bold", pad=14)
    # 막대가 아래로 갈수록 길어지므로 우상단이 비어 있다.
    # lower right 에 두면 가장 긴 막대의 라벨과 겹친다.
    ax.legend(frameon=False, loc="upper right")
    ax.set_xlim(0, max(refs) * 1.22)
    _style(ax)

    note = _label(
        "현재 설계가 짧을수록 잃는 비율이 커진다. 30일 설계는 수용 불가.",
        "Shorter current designs lose proportionally more. 30d is not feasible.",
        korean)
    fig.text(0.5, 0.015, note, ha="center", fontsize=8.5, color="#666666")

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path = _ensure(out_dir) / "fig3_equivalent_days.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path, dict(zip(refs, kept))


# =============================================================================
# 차트 4 — 임계 배수의 불확실성 구간
# =============================================================================

def chart_uncertainty(base, out_dir, korean=True, iterations=600):
    """점 추정값의 크기가 판정의 확실성을 보장하지 않음을 보여준다.

    30일 기준의 임계값은 시장 배수를 54% 웃돌아 판정이 확실해 보이지만,
    임계값 자체가 넓게 흔들리며 중앙값은 시장 배수보다 낮다.
    표로는 이 관계가 보이지 않는다.
    """
    refs = [30, 60, 90, 180, 365]
    samples, dets, probs = [], [], []
    for r in refs:
        mc = sn.monte_carlo_critical_ratio(base, r, iterations=iterations)
        samples.append([s for s in mc.samples if s is not None])
        dets.append(mc.deterministic)
        probs.append(mc.prob_below(base.ratio_r) * 100)

    fig, ax = plt.subplots(figsize=(9.5, 5.2))
    bp = ax.boxplot(samples, orientation="vertical", widths=0.55,
                    patch_artist=True, showfliers=False, zorder=3)
    for box in bp["boxes"]:
        box.set(facecolor=C_ARCHIVE, edgecolor=C_FLASH, linewidth=1.2)
    for part in ("whiskers", "caps", "medians"):
        for item in bp[part]:
            item.set(color=C_FLASH, linewidth=1.2)

    x = np.arange(1, len(refs) + 1)
    ax.scatter(x, dets, color=C_MARK, s=48, zorder=5, marker="D",
               label=_label("단일 계산값", "Point estimate", korean))
    ax.axhline(base.ratio_r, color="#333333", linestyle="--", linewidth=1.4,
               label=_label(f"현재 시장 {base.ratio_r:g}배",
                            f"Current market {base.ratio_r:g}x", korean))

    # 확률 라벨은 각 상자의 상단 수염 위에 둔다.
    # 고정 높이에 두면 범례와 겹치고, 상자 높이가 제각각이라 위치가 어긋난다.
    top = ax.get_ylim()[1]
    for i, (p, s) in enumerate(zip(probs, samples)):
        y = max(s) + top * 0.03
        ax.text(i + 1, y, f"{p:.0f}%",
                ha="center", fontsize=10, fontweight="bold",
                color=C_MARK if p < 90 else "#2e7d32")
    ax.set_ylim(top=top * 1.12)

    ax.set_xticks(x)
    ax.set_xticklabels([_label(f"{r}일", f"{r}d", korean) for r in refs])
    ax.set_xlabel(_label("즉시 검색 기간", "Searchable period", korean))
    ax.set_ylabel(_label("3사이트 수용에 필요한 단가 격차 (배)",
                         "Price ratio required for 3-site (x)", korean))
    ax.set_title(_label(
        "단일 계산값이 아니라 분포 폭이 판정을 정한다",
        "The spread decides the verdict, not the point estimate", korean),
        fontsize=13, fontweight="bold", pad=14)
    ax.legend(frameon=False, loc="upper right")
    _style(ax)

    note = _label(
        "상자가 점선 아래에 있으면 수용 가능. 상단 숫자는 수용 가능 확률.",
        "Box below the line means feasible. Top figures are feasibility probability.",
        korean)
    fig.text(0.5, 0.015, note, ha="center", fontsize=8.5, color="#666666")

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path = _ensure(out_dir) / "fig4_uncertainty.png"
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path, dict(zip(refs, probs))


# =============================================================================
# 실행
# =============================================================================

def main(out_dir=None):
    korean = setup_font()

    if out_dir is None:
        out_dir = Path(__file__).resolve().parent.parent / "outputs" / "figures"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ls = load()
    base = cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90, years=5,
        archive_krw_per_tb=cm.archive_baseline(ls.media),
        oem_premium=ls.media.get_float("array_oem_premium", "base"),
    )

    print("[차트 생성]")
    p1, totals = chart_cost_curve(base, out_dir, korean)
    print(f"  1. {p1.name}  — 90일 총액 {totals[90]:.2f}백만원")

    p2, maps = chart_cost_map(base, out_dir, korean)
    print(f"  2. {p2.name}  — 격자 {maps[ov.SITE_SINGLE].shape}")

    p3, kept = chart_equivalent_days(base, out_dir, korean)
    print(f"  3. {p3.name}  — 90일 기준 유지 {kept[90]}일")

    p4, probs = chart_uncertainty(base, out_dir, korean)
    print(f"  4. {p4.name}  — 30일 수용 확률 {probs[30]:.1f}%")

    return {"cost_curve": totals, "kept_days": kept, "probs": probs}


if __name__ == "__main__":
    main()
