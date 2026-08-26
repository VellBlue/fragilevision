"""Consensus and inter-annotator agreement for human ground truth.

One person labelling their own dataset alone produces a ground truth that
cannot be distinguished from that person's habits. Everything here exists to
answer a single question about the labels themselves: would somebody else have
written the same ones?

The module is deliberately pure — it receives judgements as dictionaries and
returns numbers — so every statistic in the interface can be recomputed from an
exported Replay Bundle with the standard library alone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import random
import statistics
from typing import Any, Callable, Iterable, Sequence

from .metrics import percentile


LABEL_VALUES = ("yes", "no", "uncertain", "exclude")
DECIDABLE = {"yes", "no"}

# Agreement states, from the weakest evidence to the strongest.
CONSENSUS_STATES = ("single", "conflict", "majority", "unanimous", "adjudicated")

# Common practice is to double-annotate a subsample rather than the whole set:
# reliability is estimated on the overlap, and the rest rides on that estimate.
RELIABILITY_SUBSAMPLE = 0.20
MIN_RELIABILITY_UNITS = 20

BOOTSTRAP_SEED = 20260825
BOOTSTRAP_RESAMPLES = 800
BOOTSTRAP_RESAMPLES_SECONDARY = 300

MAX_ANNOTATORS = 24


def normalize_annotator(name: str) -> str:
    """Collapse a reviewer name to the form stored in the database."""
    cleaned = " ".join(str(name or "").split())[:60].strip()
    return cleaned or "revisore"


# ---------------------------------------------------------------------------
# Consensus
# ---------------------------------------------------------------------------

def _merge_notes(labels: Sequence[dict[str, Any]]) -> str:
    parts = [f"{label['annotator']}: {str(label.get('note') or '').strip()}"
             for label in labels if str(label.get("note") or "").strip()]
    return " · ".join(parts)[:2000]


def resolve_labels(labels: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Collapse every judgement on one case into the single ground-truth row.

    The rule refuses to invent a winner. A plurality is not a consensus, and an
    even split is not evidence: both come back as `conflict` carrying the value
    `uncertain`, which the accuracy metrics already exclude. A disagreement that
    nobody has resolved must not quietly become a benchmark number — it has to
    be adjudicated by a person first, or stay out.
    """
    independent = [label for label in labels if not label.get("is_adjudication")]
    adjudications = [label for label in labels if label.get("is_adjudication")]
    if not independent and not adjudications:
        return None

    counts = Counter(str(label["value"]) for label in independent)
    total = sum(counts.values())
    base = {"label_count": total, "distinct_values": len(counts),
            "adjudicated_by": None, "note": _merge_notes(independent)}

    if adjudications:
        # A later adjudication overrides an earlier one: the record keeps both,
        # the ground truth follows the most recent human decision.
        chosen = max(adjudications, key=lambda label: (float(label.get("updated_at") or 0.0),
                                                       str(label["annotator"])))
        return base | {"value": str(chosen["value"]), "agreement": "adjudicated",
                       "annotator": str(chosen["annotator"]),
                       "adjudicated_by": str(chosen["annotator"]),
                       "note": _merge_notes([*independent, chosen])}
    if total == 1:
        only = independent[0]
        return base | {"value": str(only["value"]), "agreement": "single",
                       "annotator": str(only["annotator"])}
    panel = "consenso di " + ", ".join(sorted({str(label["annotator"]) for label in independent}))
    if len(counts) == 1:
        return base | {"value": next(iter(counts)), "agreement": "unanimous", "annotator": panel[:100]}
    top, top_count = counts.most_common(1)[0]
    if top_count * 2 > total:
        return base | {"value": top, "agreement": "majority", "annotator": panel[:100]}
    return base | {"value": "uncertain", "agreement": "conflict", "annotator": panel[:100]}


def consensus_summary(states: Iterable[str]) -> dict[str, Any]:
    counts = Counter(str(state or "single") for state in states)
    total = sum(counts.values())
    verified = total - counts.get("single", 0)
    return {
        "cases": total,
        **{state: counts.get(state, 0) for state in CONSENSUS_STATES},
        "verified": verified,
        "verified_share": verified / total if total else 0.0,
        "open_conflicts": counts.get("conflict", 0),
    }


# ---------------------------------------------------------------------------
# Agreement coefficients
# ---------------------------------------------------------------------------

Unit = tuple[str, ...]


def observed_agreement(units: Sequence[Unit]) -> float | None:
    """Share of agreeing pairs among all pairs of labels on the same case."""
    agree = total = 0
    for values in units:
        size = len(values)
        if size < 2:
            continue
        total += size * (size - 1) // 2
        agree += sum(count * (count - 1) // 2 for count in Counter(values).values())
    return agree / total if total else None


def krippendorff_alpha(units: Sequence[Unit]) -> float | None:
    """Nominal Krippendorff's alpha.

    Chosen as the headline coefficient because it is the only one of the three
    that survives the shape of real annotation work: reviewers who label
    different, partially overlapping subsets, and a panel whose size changes
    from case to case. Fleiss' kappa requires a constant panel and simply
    cannot be computed on that data.
    """
    coincidence: Counter[tuple[str, str]] = Counter()
    for values in units:
        size = len(values)
        if size < 2:
            continue
        counts = Counter(values)
        for first, first_count in counts.items():
            for second, second_count in counts.items():
                pairs = first_count * (first_count - 1) if first == second else first_count * second_count
                coincidence[(first, second)] += pairs / (size - 1)
    if not coincidence:
        return None
    marginal: Counter[str] = Counter()
    for (first, _), value in coincidence.items():
        marginal[first] += value
    total = sum(marginal.values())
    if total < 2:
        return None
    disagreement = sum(value for (first, second), value in coincidence.items() if first != second)
    expected = sum(first_count * second_count
                   for first, first_count in marginal.items()
                   for second, second_count in marginal.items() if first != second) / (total - 1)
    if expected <= 0:
        # Every reviewer used one and the same category: there is nothing left
        # for chance to explain, so no coefficient is defined.
        return None
    return 1 - disagreement / expected


def fleiss_kappa(units: Sequence[Unit]) -> float | None:
    """Fleiss' kappa, defined only when every case carries the same panel size."""
    usable = [values for values in units if len(values) >= 2]
    if len(usable) < 2:
        return None
    sizes = {len(values) for values in usable}
    if len(sizes) != 1:
        return None
    size = sizes.pop()
    categories = sorted({value for values in usable for value in values})
    cases = len(usable)
    proportions = {category: sum(values.count(category) for values in usable) / (cases * size)
                   for category in categories}
    per_case = [(sum(values.count(category) ** 2 for category in categories) - size) / (size * (size - 1))
                for values in usable]
    expected = sum(value * value for value in proportions.values())
    if expected >= 1:
        return None
    return (statistics.mean(per_case) - expected) / (1 - expected)


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa for two reviewers on the cases they both judged."""
    total = len(pairs)
    if total < 2:
        return None
    agreed = sum(first == second for first, second in pairs)
    first_counts = Counter(first for first, _ in pairs)
    second_counts = Counter(second for _, second in pairs)
    expected = sum((first_counts[category] / total) * (second_counts[category] / total)
                   for category in set(first_counts) | set(second_counts))
    if expected >= 1:
        # The kappa paradox in its purest form: both reviewers answered the same
        # single category every time. Perfect agreement, undefined coefficient —
        # percent agreement is reported next to it precisely for this case.
        return None
    return (agreed / total - expected) / (1 - expected)


def bootstrap_interval(units: Sequence[Any], estimator: Callable[[Sequence[Any]], float | None], *,
                       resamples: int = BOOTSTRAP_RESAMPLES,
                       seed: int = BOOTSTRAP_SEED) -> tuple[float | None, float | None]:
    """Percentile interval obtained by resampling whole cases.

    Cases are the resampled unit, not individual labels: two judgements on one
    photograph are not two independent observations of reviewer behaviour.
    Seeded, so the same annotations always yield the same interval.
    """
    total = len(units)
    if total < 4 or estimator(units) is None:
        return (None, None)
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(resamples):
        sample = [units[generator.randrange(total)] for _ in range(total)]
        value = estimator(sample)
        if value is not None:
            estimates.append(value)
    if len(estimates) < resamples // 2:
        return (None, None)
    return (percentile(estimates, 0.025), percentile(estimates, 0.975))


def interpret(value: float | None) -> str:
    """Landis & Koch labels, which are conventions and not thresholds of truth."""
    if value is None:
        return "non definito"
    if value < 0:
        return "peggiore del caso"
    if value < 0.21:
        return "scarso"
    if value < 0.41:
        return "discreto"
    if value < 0.61:
        return "moderato"
    if value < 0.81:
        return "sostanziale"
    return "quasi perfetto"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _units(labels: Sequence[dict[str, Any]]) -> dict[tuple[int, int], list[dict[str, Any]]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        if label.get("is_adjudication"):
            continue
        grouped[(int(label["image_id"]), int(label["question_id"]))].append(label)
    return grouped


def _coefficients(units: Sequence[Unit], *, resamples: int) -> dict[str, Any]:
    multi = [values for values in units if len(values) >= 2]
    alpha = krippendorff_alpha(multi)
    low, high = bootstrap_interval(multi, krippendorff_alpha, resamples=resamples)
    return {
        "reliability_units": len(multi),
        "labels": sum(len(values) for values in multi),
        "panel_sizes": dict(sorted(Counter(len(values) for values in multi).items())),
        "percent_agreement": observed_agreement(multi),
        "krippendorff_alpha": alpha,
        "alpha_ci_low": low, "alpha_ci_high": high,
        "alpha_label": interpret(alpha),
        "fleiss_kappa": fleiss_kappa(multi),
    }


def _pairwise(labels: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_annotator: dict[str, dict[tuple[int, int], str]] = defaultdict(dict)
    for label in labels:
        if label.get("is_adjudication"):
            continue
        by_annotator[str(label["annotator"])][(int(label["image_id"]), int(label["question_id"]))] = str(label["value"])
    output = []
    for first, second in combinations(sorted(by_annotator), 2):
        shared = sorted(by_annotator[first].keys() & by_annotator[second].keys())
        pairs = [(by_annotator[first][key], by_annotator[second][key]) for key in shared]
        kappa = cohen_kappa(pairs)
        low, high = bootstrap_interval(pairs, cohen_kappa, resamples=BOOTSTRAP_RESAMPLES_SECONDARY)
        matrix: Counter[tuple[str, str]] = Counter(pairs)
        output.append({
            "a": first, "b": second, "shared": len(shared),
            "agreed": sum(one == other for one, other in pairs),
            "percent_agreement": (sum(one == other for one, other in pairs) / len(pairs)) if pairs else None,
            "cohen_kappa": kappa, "kappa_ci_low": low, "kappa_ci_high": high,
            "kappa_label": interpret(kappa),
            "confusions": [{"a_value": one, "b_value": other, "count": count}
                           for (one, other), count in matrix.most_common()
                           if one != other][:8],
        })
    return sorted(output, key=lambda item: (-item["shared"], item["a"], item["b"]))


def calculate_agreement(labels: Sequence[dict[str, Any]], questions: Sequence[dict[str, Any]],
                        consensus_states: Sequence[str] = ()) -> dict[str, Any]:
    """Full reliability report for one project's human judgements."""
    independent = [label for label in labels if not label.get("is_adjudication")]
    grouped = _units(labels)
    all_units = [tuple(str(label["value"]) for label in members) for members in grouped.values()]
    overall = _coefficients(all_units, resamples=BOOTSTRAP_RESAMPLES)

    annotators: list[dict[str, Any]] = []
    by_annotator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for label in independent:
        by_annotator[str(label["annotator"])].append(label)
    for name, own in sorted(by_annotator.items(), key=lambda item: (-len(item[1]), item[0])):
        distribution = Counter(str(label["value"]) for label in own)
        decidable = sum(distribution[value] for value in DECIDABLE)
        annotators.append({
            "annotator": name, "labels": len(own),
            "images": len({int(label["image_id"]) for label in own}),
            "questions": len({int(label["question_id"]) for label in own}),
            "distribution": {value: distribution.get(value, 0) for value in LABEL_VALUES},
            # A reviewer who answers "yes" to nearly everything drags every
            # coefficient down without ever disagreeing loudly.
            "positive_share": distribution["yes"] / decidable if decidable else None,
            "adjudications": sum(1 for label in labels
                                 if label.get("is_adjudication") and str(label["annotator"]) == name),
            "last_seen": max(float(label.get("updated_at") or 0.0) for label in own),
        })

    per_question = []
    for question in questions:
        question_id = int(question["id"])
        question_units = [tuple(str(label["value"]) for label in members)
                          for key, members in grouped.items() if key[1] == question_id]
        coefficients = _coefficients(question_units, resamples=BOOTSTRAP_RESAMPLES_SECONDARY)
        per_question.append({
            "question_id": question_id, "key": str(question.get("key") or ""),
            "label": str(question.get("label") or ""),
            "annotated_cases": len(question_units),
            "annotators": len({str(label["annotator"]) for key, members in grouped.items()
                               if key[1] == question_id for label in members}),
            **coefficients,
        })

    consensus = consensus_summary(consensus_states)
    warnings = _warnings(overall, annotators, per_question, consensus)
    return {
        "annotators": annotators[:MAX_ANNOTATORS],
        "annotator_count": len(annotators),
        "annotated_cases": len(grouped),
        "overall": overall, "questions": per_question,
        "pairs": _pairwise(labels), "consensus": consensus,
        "warnings": warnings,
        "method": ("Alpha di Krippendorff nominale come coefficiente principale: regge revisori con "
                   "sottoinsiemi diversi e panel di dimensione variabile. Il kappa di Fleiss compare "
                   "solo quando ogni caso ha lo stesso numero di revisori, il kappa di Cohen soltanto "
                   "a coppie. Gli intervalli al 95% vengono da un bootstrap percentile sui casi, non "
                   "sulle singole etichette: due giudizi sulla stessa fotografia non sono due "
                   "osservazioni indipendenti."),
        "limitations": ("I coefficienti misurano quanto i revisori si somigliano, non quanto hanno "
                        "ragione: un gruppo che condivide lo stesso pregiudizio ottiene un alpha alto. "
                        "Con poche decine di casi in doppio l’intervallo resta largo, e le etichette "
                        "“moderato” o “sostanziale” sono convenzioni della letteratura, non soglie di "
                        "verità."),
    }


def _warnings(overall: dict[str, Any], annotators: list[dict[str, Any]],
              questions: list[dict[str, Any]], consensus: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if len(annotators) < 2:
        warnings.append({"severity": "alta", "area": "revisori",
                         "text": "Un solo revisore: la verità di riferimento non è distinguibile dalle "
                                 "abitudini di chi l’ha scritta e nessun coefficiente di accordo è calcolabile."})
    elif overall["reliability_units"] < MIN_RELIABILITY_UNITS:
        warnings.append({"severity": "media", "area": "revisori",
                         "text": f"Solo {overall['reliability_units']} casi giudicati da più di un revisore: "
                                 f"sotto i {MIN_RELIABILITY_UNITS} l’intervallo sull’accordo è troppo largo "
                                 "per dire qualcosa."})
    alpha = overall.get("krippendorff_alpha")
    if alpha is not None and alpha < 0.67:
        warnings.append({"severity": "alta" if alpha < 0.4 else "media", "area": "accordo",
                         "text": f"Alpha di Krippendorff {alpha:.2f} ({interpret(alpha)}): la soglia "
                                 "convenzionale per trarre conclusioni è 0,80, e 0,67 è il minimo per "
                                 "conclusioni provvisorie. Il problema è quasi sempre una domanda mal "
                                 "definita, non un revisore distratto."})
    if consensus["open_conflicts"]:
        warnings.append({"severity": "media", "area": "conflitti",
                         "text": f"{consensus['open_conflicts']} casi in conflitto non risolto: restano "
                                 "fuori dall’accuratezza finché una persona non li giudica."})
    for question in questions:
        value = question.get("krippendorff_alpha")
        if value is not None and value < 0.4 and question["reliability_units"] >= 10:
            warnings.append({"severity": "media", "area": f"domanda “{question['label']}”",
                             "text": f"accordo {interpret(value)} (alpha {value:.2f}) su "
                                     f"{question['reliability_units']} casi in doppio: se davanti a un "
                                     "disaccordo devi fermarti a pensare se il tag si applica, il tag è "
                                     "mal definito."})
    for annotator in annotators:
        share = annotator.get("positive_share")
        if share is not None and annotator["labels"] >= 20 and (share > 0.9 or share < 0.1):
            warnings.append({"severity": "bassa", "area": f"revisore “{annotator['annotator']}”",
                             "text": f"risponde {'sì' if share > .5 else 'no'} nel "
                                     f"{100 * max(share, 1 - share):.0f}% dei casi decidibili: un revisore "
                                     "quasi costante gonfia l’accordo osservato senza aggiungere informazione."})
    order = {"alta": 0, "media": 1, "bassa": 2}
    warnings.sort(key=lambda item: order.get(item["severity"], 3))
    return warnings
