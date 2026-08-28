"""Does a group of indicators earn its place? Asked twice, from both directions.

Selection frequency does not answer this. A column that survives the filter on
every one of 250 sessions has only demonstrated that it correlated with the
target inside the window it was picked in, which is what the filter selects for.
Sign stability says no more: it is a statement about the fit, not about value.

Two questions do answer it, and each gets its own walk-forward run over the same
250 sessions:

    ablation           remove the group from the full set. A record that does
                       not get worse means the group was carrying nothing.
    incremental value  add the group to the own-price columns alone. A record
                       that does not get better means the group adds nothing.

They can disagree, and the disagreement is the informative case. A group that
duplicates another loses nothing on removal -- the duplicate covers for it --
while still carrying real signal on its own. Reporting ablation alone would call
that group worthless; reporting incremental alone would double-count it.

Everything is measured against a control on the same common dates, on the
metric with the largest sample. Direction accuracy rests on 5,500 predictions
and rank IC on 250 sessions; trade counts here are in the low hundreds and are
reported but never used to decide.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from research.evaluation import Prediction, session_selection, spearman


@dataclass(frozen=True, slots=True)
class Change:
    """One arm against its control, on the predictions they share."""

    group: str
    kind: str  # "ablation" or "incremental"
    pairs: int
    direction_delta_pp: float
    rank_ic_delta: float
    rank_ic_t: float | None
    spearman_delta: float
    paired_p: float | None

    @property
    def helped(self) -> bool:
        """Whether the group carried something, in this arm's own direction.

        Ablation: removing it made the record worse, so the negative delta is
        the evidence. Incremental: adding it made the record better.
        """

        return (
            self.direction_delta_pp < 0
            if self.kind == "ablation"
            else self.direction_delta_pp > 0
        )


def _paired_sign_test(left: Sequence[bool], right: Sequence[bool]) -> float | None:
    """Two-sided p for "these two disagree in one direction more than chance".

    Only the predictions where exactly one arm was right carry information; the
    ones both got right, or both got wrong, say nothing about which is better.
    Comparing two aggregate accuracies instead throws that pairing away.
    """

    wins = sum(1 for a, b in zip(left, right, strict=True) if a and not b)
    losses = sum(1 for a, b in zip(left, right, strict=True) if b and not a)
    trials = wins + losses
    if trials == 0:
        return None
    z = (wins - trials * 0.5) / math.sqrt(trials * 0.25)
    return float(math.erfc(abs(z) / math.sqrt(2)))


def _rank_ic(predictions: Sequence[Prediction]) -> list[float]:
    return [
        session.rank_ic
        for session in session_selection(predictions)
        if session.rank_ic is not None
    ]


def _t(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    array = np.asarray(values, dtype=float)
    deviation = array.std(ddof=1)
    if deviation == 0:
        return None
    return float(array.mean() / (deviation / math.sqrt(len(array))))


def compare(
    arm: Sequence[Prediction],
    control: Sequence[Prediction],
    *,
    group: str,
    kind: str,
) -> Change:
    """One arm against one control, restricted to the pairs both produced."""

    left = {(row.date, row.ticker): row for row in arm}
    right = {(row.date, row.ticker): row for row in control}
    shared = sorted(set(left) & set(right))
    if not shared:
        return Change(group, kind, 0, 0.0, 0.0, None, 0.0, None)

    arm_rows = [left[key] for key in shared]
    control_rows = [right[key] for key in shared]

    arm_hits = [row.direction_correct for row in arm_rows]
    control_hits = [row.direction_correct for row in control_rows]

    arm_ic = _rank_ic(arm_rows)
    control_ic = _rank_ic(control_rows)
    paired = [
        a - b for a, b in zip(arm_ic, control_ic, strict=False)
    ]

    arm_spearman = spearman(
        np.array([row.predicted_return for row in arm_rows]),
        np.array([row.actual_return for row in arm_rows]),
    )
    control_spearman = spearman(
        np.array([row.predicted_return for row in control_rows]),
        np.array([row.actual_return for row in control_rows]),
    )

    return Change(
        group=group,
        kind=kind,
        pairs=len(shared),
        direction_delta_pp=(float(np.mean(arm_hits)) - float(np.mean(control_hits)))
        * 100,
        rank_ic_delta=(
            float(np.mean(arm_ic)) - float(np.mean(control_ic))
            if arm_ic and control_ic
            else 0.0
        ),
        rank_ic_t=_t(paired),
        spearman_delta=(
            (arm_spearman or 0.0) - (control_spearman or 0.0)
        ),
        paired_p=_paired_sign_test(arm_hits, control_hits),
    )


def report(ablations: Sequence[Change], increments: Sequence[Change]) -> list[str]:
    lines: list[str] = []

    lines += [
        "【Ablation】compact から1グループ抜く。悪化したなら、そのグループは効いていた",
        "",
        f"  {'抜いたグループ':<24}{'方向差(pp)':>12}{'順位IC差':>11}"
        f"{'t値':>8}{'符号検定p':>11}{'判定':>10}",
        "  " + "-" * 78,
    ]
    for item in sorted(ablations, key=lambda c: c.direction_delta_pp):
        verdict = "効いていた" if item.helped else "抜いても同じ"
        lines.append(
            f"  {item.group:<24}{item.direction_delta_pp:>+12.2f}"
            f"{item.rank_ic_delta:>+11.4f}"
            f"{(item.rank_ic_t if item.rank_ic_t is not None else float('nan')):>+8.2f}"
            f"{(item.paired_p if item.paired_p is not None else float('nan')):>11.3f}"
            f"{verdict:>10}"
        )

    lines += [
        "",
        "【Incremental Value】自銘柄の価格特徴量に1グループ足す。"
        "改善したなら単独で効く",
        "",
        f"  {'足したグループ':<24}{'方向差(pp)':>12}{'順位IC差':>11}"
        f"{'t値':>8}{'符号検定p':>11}{'判定':>10}",
        "  " + "-" * 78,
    ]
    for item in sorted(increments, key=lambda c: -c.direction_delta_pp):
        verdict = "単独で効く" if item.helped else "足しても同じ"
        lines.append(
            f"  {item.group:<24}{item.direction_delta_pp:>+12.2f}"
            f"{item.rank_ic_delta:>+11.4f}"
            f"{(item.rank_ic_t if item.rank_ic_t is not None else float('nan')):>+8.2f}"
            f"{(item.paired_p if item.paired_p is not None else float('nan')):>11.3f}"
            f"{verdict:>10}"
        )

    family = [
        item
        for item in list(ablations) + list(increments)
        if item.paired_p is not None
    ]
    if family:
        from research.multiple_testing import benjamini_hochberg

        adjusted = benjamini_hochberg([item.paired_p for item in family])
        survivors = [
            (item, q)
            for item, q in zip(family, adjusted, strict=True)
            if q < 0.05
        ]
        lines += [
            "",
            f"【多重比較の補正】{len(family)}回の比較をまとめてBH-FDR補正",
            "",
        ]
        if survivors:
            for item, q in sorted(survivors, key=lambda pair: pair[1]):
                label = "抜くと悪化" if item.kind == "ablation" else "足すと改善"
                if not item.helped:
                    label = "抜いても同じ" if item.kind == "ablation" else "足すと悪化"
                lines.append(
                    f"  {item.group}（{item.kind}）: raw p={item.paired_p:.4f} "
                    f"→ q={q:.4f}　{label}"
                )
        else:
            lines.append(
                "  補正後に q<0.05 を満たす比較はありません。"
                "個別のpだけを見て採否を決めることはできません。"
            )

    lines += ["", "【両者の食い違い】", ""]
    by_group = {item.group: item for item in ablations}
    disagreements = 0
    for increment in increments:
        ablation = by_group.get(increment.group)
        if ablation is None or ablation.helped == increment.helped:
            continue
        disagreements += 1
        if increment.helped:
            lines.append(
                f"  {increment.group}: 単独では効くが抜いても悪化しない。"
                "他のグループが同じ情報を持っています（冗長）。"
            )
        else:
            lines.append(
                f"  {increment.group}: 抜くと悪化するが、単独では効かない。"
                "他と組み合わさって初めて効きます。"
            )
    if not disagreements:
        lines.append("  なし。両方向の判定が全グループで一致しました。")

    lines += [
        "",
        "  判定は最も標本の大きい指標（方向的中5,500件・順位IC250営業日）で行い、",
        "  取引損益では行っていません。符号検定は片方だけが当たった予測を数えます。",
        "  BH補正は21回の比較を1つの族として扱っています。各比較は同じ対照を共有して",
        "  いるので独立ではありませんが、正の依存の下でもBHのFDR制御は成立します。",
    ]
    return lines


__all__ = ["Change", "compare", "report"]
