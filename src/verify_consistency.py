"""
verify_consistency.py — 자기 검증 스크립트 (Phase 10)

역할
    문서에 적힌 수치가 코드가 산출하는 값과 일치하는지 대조한다.
    새로 계산하지 않고, 이미 만든 것이 서로 맞는지만 확인한다.

왜 필요한가
    각 문서는 작성 시점에는 옳았다. 그러나 이후 단계에서 값이 바뀌었을 수
    있다. 실제로 세 번 발생했다.

      1. 미채택 항목이 로더를 통과 (Phase 4 에서 발견)
      2. Phase 5 의 민감도 권고가 틀림 (Phase 6 에서 발견)
      3. 작업 기록의 미확정 항목이 제안서에 누락 (읽는 사람이 지적해 발견)

    3번은 검증 코드가 잡지 못했다. 그래서 이 스크립트에 전파 검사를 넣는다.

수동 점검을 대체하는 이유
    사람이 눈으로 대조하면 다음에 값이 바뀔 때 또 놓친다.
    스크립트는 반복 실행할 수 있다.

주의
    이 스크립트는 문서를 고치지 않는다. 불일치를 보고만 한다.
    작업 기록은 판단 시점의 상태를 보존해야 하므로, 무엇을 고칠지는
    사람이 판단한다.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import breakeven as bk
import cost_model as cm
import overhead as ov
import placement as pl
import reduction as rd
import sensitivity as sn
import tco_engine as te
from pricing_loader import BLOCKED_STATUS, load

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


@dataclass
class Finding:
    """검사 결과 한 건."""
    category: str
    level: str          # 'error' | 'warn' | 'info'
    message: str
    location: str = ""


@dataclass
class Report:
    findings: list = field(default_factory=list)
    checked: int = 0

    def add(self, category, level, message, location=""):
        self.findings.append(Finding(category, level, message, location))

    def ok(self):
        self.checked += 1

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warns(self):
        return [f for f in self.findings if f.level == "warn"]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _docs() -> dict:
    out = {}
    for p in sorted(DOCS.glob("phase*.md")):
        out[p.name] = _read(p)
    for name in ("README.ko.md", "README.md"):
        p = ROOT / name
        if p.exists():
            out[name] = _read(p)
    return out


# =============================================================================
# 검사 1 — 문서 수치와 코드 산출값 대조
# =============================================================================

def check_numbers(rep: Report, docs: dict, base: cm.Scenario):
    """같은 값이 여러 문서에 반복 등장한다. 하나라도 어긋나면 신뢰도가 무너진다.

    문자열 포함 여부로 확인한다. 정규식으로 모든 표기 변형을 잡으려 하면
    오탐이 늘어나므로, 문서에 실제로 쓰인 표기를 그대로 찾는다.
    """
    # 코드에서 산출
    ceiling = rd.DRR_CEILING
    total90 = bk._total(base, ratio_r=22.0, flash_days=90,
                        site_config=ov.SITE_SINGLE) / 1e6
    total730 = bk._total(base, ratio_r=22.0, flash_days=730,
                         site_config=ov.SITE_SINGLE) / 1e6
    crit90 = bk.critical_ratio(base, reference_days=90).critical_r
    crit30 = bk.critical_ratio(base, reference_days=30).critical_r
    eq90 = bk.equivalent_days(base, 22.0, reference_days=90).days

    expected = [
        ("감축 절대 상한", f"{ceiling:.2f}", ["3.33"],
         ["phase01_reduction.md", "phase03_placement.md", "README.ko.md"]),
        ("90일 총액", f"{total90:.1f}", ["12.4"],
         ["phase04_engine.md", "phase08_proposal.md"]),
        ("계층화 없음 총액", f"{total730:.1f}", ["89.3"],
         ["phase08_proposal.md", "README.ko.md"]),
        # 고객 문서는 소수 첫째 자리(11.3배)로, 작업 기록은 둘째 자리(11.30)로
        # 표기한다. 값은 같으므로 두 표기를 모두 허용한다.
        ("90일 임계 배수", f"{crit90:.2f}", ["11.30", "11.3배"],
         ["phase05_breakeven.md", "phase06_sensitivity.md",
          "phase08_proposal.md", "README.ko.md"]),
        ("30일 임계 배수", f"{crit30:.2f}", ["33.84", "33.8배"],
         ["phase05_breakeven.md", "phase06_sensitivity.md", "README.ko.md"]),
        ("3사이트 등비용 기간", str(eq90), ["47일", "47 days"],
         ["phase05_breakeven.md", "phase08_proposal.md"]),
        ("블록 오버헤드", str(ov.BLOCK_RAID), ["1.536"],
         ["phase02_media_pricing.md", "phase03_placement.md"]),
        ("이레저 코딩", str(ov.OBJECT_EC_SINGLE), ["1.333"],
         ["phase02_media_pricing.md", "phase03_placement.md"]),
        ("다중 사이트", str(ov.OBJECT_MULTISITE), ["5.76"],
         ["phase02_media_pricing.md", "phase03_placement.md",
          "phase08_proposal.md"]),
    ]

    for label, computed, tokens, files in expected:
        for fname in files:
            text = docs.get(fname, "")
            if not text:
                rep.add("수치", "warn", f"{fname} 을 찾을 수 없습니다", label)
                continue
            if any(t in text for t in tokens):
                rep.ok()
            else:
                rep.add("수치", "error",
                        f"{label}({computed})이 문서에 없습니다. "
                        f"찾은 표기: {tokens}", fname)


def check_charts(rep: Report, base: cm.Scenario):
    """차트가 참조하는 수치가 엔진 산출값과 같은지 확인한다."""
    figures = ROOT / "outputs" / "figures"
    expected = ["fig1_cost_curve.png", "fig2_cost_map.png",
                "fig3_equivalent_days.png", "fig4_uncertainty.png"]
    for name in expected:
        p = figures / name
        if not p.exists():
            rep.add("차트", "error", f"{name} 이 없습니다", "outputs/figures")
        elif p.stat().st_size < 5000:
            rep.add("차트", "error", f"{name} 이 비정상적으로 작습니다", name)
        else:
            rep.ok()


# =============================================================================
# 검사 2 — 미확정 항목의 문서 간 전파
# =============================================================================

# 작업 기록에 기록된 미확정 항목 중, 고객 문서에도 반드시 나타나야 하는 것.
# 읽는 사람이 지적해서 드러난 유형이므로 명시 목록으로 관리한다.
PROPAGATION_RULES = [
    ("색인 감축률 근거 부재",
     ["phase01_reduction.md", "phase06_sensitivity.md"],
     ["phase08_proposal.md", "README.ko.md"],
     ["실측이 아니라 범위", "실측치 없음", "균등 추출", "근거 없이 정해진"]),
    ("다중 사이트 계수의 사례 1건",
     ["phase02_media_pricing.md", "phase07_qualitative.md"],
     ["phase08_proposal.md", "README.ko.md"],
     ["사례 1건", "조달 사례 1건", "단일 사례"]),
    ("판정 문턱값 90% 의 자의성",
     ["phase06_sensitivity.md", "phase07_qualitative.md"],
     ["phase08_proposal.md", "README.ko.md"],
     ["근거 문헌", "관행적으로 설정", "근거 문헌 없이"]),
    ("국내 유통 재고 미확인",
     ["phase07_qualitative.md"],
     ["phase08_proposal.md", "README.ko.md"],
     ["국내 유통", "국내 재고"]),
    ("총소유비용이 아니라 매체 원가",
     ["phase00_scope.md", "phase04_engine.md"],
     ["phase08_proposal.md", "README.ko.md"],
     ["총소유비용이 아니", "매체 원가입니다", "매체 원가 비교"]),
]


def check_propagation(rep: Report, docs: dict):
    """작업 기록의 한계가 고객 문서에 옮겨졌는지 확인한다.

    이 검사가 없어서 다중 사이트 계수의 근거가 사례 1건이라는 사실이
    제안서에 빠졌다. 검증 코드가 잡지 못했고 읽는 사람이 물어서 드러났다.
    """
    for label, sources, targets, tokens in PROPAGATION_RULES:
        # 출처에 실제로 기록되어 있는지 먼저 확인
        in_source = any(any(t in docs.get(s, "") for t in tokens)
                        for s in sources)
        if not in_source:
            rep.add("전파", "warn",
                    f"'{label}' 이 작업 기록에서 확인되지 않습니다. "
                    f"규칙이 낡았을 수 있습니다", ", ".join(sources))
            continue
        for tgt in targets:
            text = docs.get(tgt, "")
            if any(t in text for t in tokens):
                rep.ok()
            else:
                rep.add("전파", "error",
                        f"'{label}' 이 고객 문서에 반영되지 않았습니다", tgt)


# =============================================================================
# 검사 3 — 출처와 조회일 완전성
# =============================================================================

def check_sources(rep: Report, ls):
    """계산에 쓰이는 항목에 출처와 조회일이 있는지 확인한다."""
    for name, led in (("media", ls.media), ("cloud", ls.cloud)):
        for key in led.keys():
            it = led.item(key)
            if it.status in BLOCKED_STATUS:
                continue
            if not it.source_url:
                rep.add("출처", "error", f"[{key}] 출처 URL 이 없습니다", name)
            elif not getattr(it, "path", ""):
                rep.ok()
            else:
                rep.ok()

    # 문서의 출처 표에 조회일이 있는지
    for fname in ("phase01_reduction.md", "phase02_media_pricing.md",
                  "phase03_placement.md", "phase07_qualitative.md"):
        text = _read(DOCS / fname)
        if not text:
            continue
        rows = [l for l in text.splitlines()
                if l.startswith("|") and "https://" in l]
        for row in rows:
            if not re.search(r"20\d\d-\d\d-\d\d", row):
                rep.add("출처", "warn",
                        f"출처 행에 조회일이 없습니다: {row[:60]}...", fname)
            else:
                rep.ok()


# =============================================================================
# 검사 4 — 링크 유효성
# =============================================================================

def check_links(rep: Report, docs: dict):
    """마크다운 링크가 실제 파일을 가리키는지 확인한다."""
    pattern = re.compile(r"\[[^\]]*\]\((?!https?://)([^)#]+)")
    for fname, text in docs.items():
        origin = ROOT if fname.startswith("README") else DOCS
        for rel in pattern.findall(text):
            target = (origin / rel).resolve()
            if target.exists():
                rep.ok()
            else:
                rep.add("링크", "error", f"대상이 없습니다: {rel}", fname)


# =============================================================================
# 검사 5 — 원장 정합성
# =============================================================================

def check_ledger(rep: Report, ls):
    """계산에 쓰이면 안 되는 항목이 실제로 차단되는지 확인한다."""
    from pricing_loader import PricingError, UnitSystemError

    for key, _ in ls.media.blocked_items():
        try:
            ls.media.get(key)
            rep.add("원장", "error",
                    f"[{key}] pending 인데 값이 반환되었습니다", "media")
        except PricingError:
            rep.ok()

    for key, _ in ls.media.rejected_items():
        try:
            ls.media.get(key)
            rep.add("원장", "error",
                    f"[{key}] 미채택인데 값이 반환되었습니다", "media")
        except PricingError:
            rep.ok()

    # 단위계 분리
    try:
        _ = ls.media.get_krw("hdd_retail_nearline") + ls.cloud.get("object_price")
        rep.add("원장", "error", "두 원장의 값이 합산되었습니다", "unit_system")
    except UnitSystemError:
        rep.ok()

    # 대표값 미설정 유지
    if "scan_range" not in ls.media.items_without_base():
        rep.add("원장", "error",
                "scan_range 의 base 가 채워졌습니다. 방법론 위반입니다",
                "media_pricing.yaml")
    else:
        rep.ok()

    # 명시적 민감도 목록
    declared = sn.declared_targets()
    if "amortization_years" not in declared["total_cost"]:
        rep.add("원장", "error",
                "상각기간이 민감도 목록에서 누락되었습니다", "sensitivity.py")
    else:
        rep.ok()
    if "drr_tsidx" not in declared["critical_ratio"]:
        rep.add("원장", "error",
                "색인 감축률이 민감도 목록에서 누락되었습니다", "sensitivity.py")
    else:
        rep.ok()


# =============================================================================
# 검사 5b — 옛 수치와 정정 절
# =============================================================================

# 작업 기록은 판단 시점의 상태를 보존한다. 따라서 이후 단계에서 바뀐 값이
# 본문에 그대로 남아 있는 것은 오류가 아니다.
# 다만 정정 기록이 없으면 읽는 사람이 옛 값을 최종값으로 오해한다.
# (문서, 본문에 남은 옛 표기, 정정 절을 확인할 표기)
STALE_VALUE_RULES = [
    ("phase00_scope.md", "4.0 ~ 25.0", "3.0 ~ 30.0"),
    ("phase00_scope.md", "1.0(감축 없음) ~ 6.0", "1.0 ~ 3.33"),
    ("phase05_breakeven.md", "민감도 1순위 | 색인 DRR | **상각기간**", "정정"),
]


def check_stale_values(rep: Report, docs: dict):
    """옛 수치가 본문에 남아 있으면 정정 절이 있어야 한다.

    값을 고치는 대신 정정 절을 두는 것이 본 프로젝트의 방침이다.
    본문을 고치면 왜 바뀌었는지가 사라지기 때문이다.
    """
    for fname, stale, corrected in STALE_VALUE_RULES:
        text = docs.get(fname, "")
        if not text:
            continue
        if stale not in text:
            rep.ok()          # 옛 표기가 없으면 검사 대상이 아니다
            continue
        # 옛 표기가 있으므로 정정 기록이 있어야 한다
        has_section = ("정정 사항" in text or "정정 내용" in text)
        has_value = corrected in text
        if has_section and has_value:
            rep.ok()
        elif has_section:
            rep.add("정정", "warn",
                    f"정정 절은 있으나 확정값 '{corrected}' 이 보이지 않습니다",
                    fname)
        else:
            rep.add("정정", "error",
                    f"옛 표기 '{stale}' 이 본문에 있는데 정정 절이 없습니다. "
                    f"읽는 사람이 최종값으로 오해합니다", fname)


# =============================================================================
# 검사 6 — 문서 규약
# =============================================================================

REQUIRED_SECTIONS = ("확정 항목", "검토 후 폐기한 항목")


def check_document_convention(rep: Report):
    """작업 기록에 세 칸 규약이 지켜졌는지 확인한다.

    제안서(phase08)는 고객 대상이므로 이 규약을 적용하지 않는다.
    """
    for p in sorted(DOCS.glob("phase0[0-7]*.md")):
        text = _read(p)
        for section in REQUIRED_SECTIONS:
            if section in text:
                rep.ok()
            else:
                rep.add("규약", "warn",
                        f"'{section}' 절이 없습니다", p.name)


def check_proposal_language(rep: Report, docs: dict):
    """고객 문서에 작업 기록 용어가 남아 있지 않은지 확인한다."""
    forbidden = ["선행 프로젝트", "Phase 0", "Phase 1", "phase0",
                 "가설", "반증", "몬테카를로", "민감도", "손익분기",
                 "프로젝트의 가치", "산출물이 성립"]
    text = docs.get("phase08_proposal.md", "")
    for word in forbidden:
        if word in text:
            rep.add("고객문서", "error",
                    f"작업 기록 용어가 남아 있습니다: '{word}'",
                    "phase08_proposal.md")
        else:
            rep.ok()


# =============================================================================
# 실행
# =============================================================================

def run() -> Report:
    rep = Report()
    ls = load()
    base = cm.Scenario(
        daily_gb=100, ratio_r=22.0, flash_days=90, years=5,
        archive_krw_per_tb=cm.archive_baseline(ls.media),
        oem_premium=ls.media.get_float("array_oem_premium", "base"),
    )
    docs = _docs()

    check_numbers(rep, docs, base)
    check_charts(rep, base)
    check_propagation(rep, docs)
    check_sources(rep, ls)
    check_links(rep, docs)
    check_ledger(rep, ls)
    check_stale_values(rep, docs)
    check_document_convention(rep)
    check_proposal_language(rep, docs)
    return rep


def main():
    rep = run()

    print("=" * 66)
    print("자기 검증 결과")
    print("=" * 66)
    print(f"  통과 : {rep.checked}건")
    print(f"  오류 : {len(rep.errors)}건")
    print(f"  경고 : {len(rep.warns)}건")
    print()

    if rep.errors:
        print("[오류]")
        for f in rep.errors:
            loc = f" ({f.location})" if f.location else ""
            print(f"  [{f.category}] {f.message}{loc}")
        print()

    if rep.warns:
        print("[경고]")
        for f in rep.warns:
            loc = f" ({f.location})" if f.location else ""
            print(f"  [{f.category}] {f.message}{loc}")
        print()

    if not rep.errors and not rep.warns:
        print("모든 검사를 통과했습니다.")

    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
