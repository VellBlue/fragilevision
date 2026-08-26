"""Transparent statistics for binary visual evaluations.

Every number shown in the UI can be reproduced from an exported run with
Python's standard library alone.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import math
import statistics
from typing import Any, Iterable


VERDICTS = {"yes", "no"}


def pct_text(value: float) -> str:
    return f"{100 * value:.0f}%"


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def exact_mcnemar_p(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value for the discordant cells."""
    discordant = b + c
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(0, min(b, c) + 1)) / (2**discordant)
    return min(1.0, 2 * tail)


def _majority(values: Iterable[str]) -> str | None:
    counts = Counter(v for v in values if v in VERDICTS)
    if not counts or counts["yes"] == counts["no"]:
        return None
    return counts.most_common(1)[0][0]


def _variant_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("ground_truth") in VERDICTS]
    parsed = [row for row in eligible if row.get("answer") in VERDICTS]
    correct = sum(row["answer"] == row["ground_truth"] for row in parsed)
    positives = [row for row in parsed if row["ground_truth"] == "yes"]
    negatives = [row for row in parsed if row["ground_truth"] == "no"]
    tpr = sum(row["answer"] == "yes" for row in positives) / len(positives) if positives else None
    tnr = sum(row["answer"] == "no" for row in negatives) / len(negatives) if negatives else None
    # With only one ground-truth class present there is no balance to compute:
    # reporting sensitivity as "balanced" would flatter a constant answer.
    balanced = statistics.mean((tpr, tnr)) if (tpr is not None and tnr is not None) else None
    low, high = wilson_interval(correct, len(parsed))
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    return {
        "samples": len(eligible), "parsed": len(parsed), "correct": correct,
        "accuracy": correct / len(parsed) if parsed else 0.0,
        "balanced_accuracy": balanced,
        "coverage": len(parsed) / len(eligible) if eligible else 0.0,
        "format_rate": sum(bool(row.get("format_valid")) for row in eligible) / len(eligible) if eligible else 0.0,
        "ci_low": low, "ci_high": high,
        "median_ms": statistics.median(latencies) if latencies else 0.0,
        "p95_ms": percentile(latencies, 0.95),
    }


def _evidence_gate(*, cases: int, independent_groups: int, questions: list[dict[str, Any]],
                   coverage: float, format_rate: float, comparisons: int,
                   majority_baseline: float, verified_share: float) -> dict[str, Any]:
    """A transparent heuristic gate: it labels evidence maturity, never model quality."""
    mutation_ready = bool(questions) and all(item["variant_count"] >= 2 for item in questions)
    # `value` stays machine-readable for the Replay Bundle; `display` is what the
    # interface shows, so a "≥ 95%" label never sits next to a bare "1".
    checks = [
        {"key": "cases", "label": "30 casi annotati", "passed": cases >= 30,
         "value": cases, "display": str(cases)},
        {"key": "groups", "label": "5 gruppi sorgente indipendenti", "passed": independent_groups >= 5,
         "value": independent_groups, "display": str(independent_groups)},
        {"key": "mutations", "label": "almeno 2 varianti per domanda", "passed": mutation_ready,
         "value": min((item["variant_count"] for item in questions), default=0),
         "display": str(min((item["variant_count"] for item in questions), default=0))},
        {"key": "coverage", "label": "copertura risposte ≥ 95%", "passed": coverage >= .95,
         "value": round(coverage, 4), "display": pct_text(coverage)},
        {"key": "format", "label": "formato valido ≥ 90%", "passed": format_rate >= .90,
         "value": round(format_rate, 4), "display": pct_text(format_rate)},
        {"key": "pairing", "label": "20 confronti appaiati", "passed": comparisons >= 20,
         "value": comparisons, "display": str(comparisons)},
        {"key": "baseline", "label": "baseline maggioritaria ≤ 80%", "passed": majority_baseline <= .80,
         "value": round(majority_baseline, 4), "display": pct_text(majority_baseline)},
        # Ground truth written by one person alone cannot be told apart from that
        # person's habits. Double-annotating a fifth of the cases is the standard
        # reliability subsample: enough to estimate agreement, not the whole set.
        {"key": "reliability", "label": "20% dei casi giudicati da più revisori", "passed": verified_share >= .20,
         "value": round(verified_share, 4), "display": pct_text(verified_share)},
    ]
    basic = mutation_ready and coverage >= .95 and format_rate >= .90 and comparisons >= 20
    if (basic and cases >= 100 and independent_groups >= 15 and comparisons >= 100
            and majority_baseline <= .80 and verified_share >= .20):
        grade, status = "A", "strong"
    elif basic and cases >= 30 and independent_groups >= 5 and majority_baseline <= .85:
        grade, status = "B", "reviewable"
    elif cases and comparisons:
        grade, status = "C", "exploratory"
    else:
        grade, status = "E", "insufficient"
    return {
        "grade": grade, "status": status,
        "passed": sum(bool(item["passed"]) for item in checks), "total": len(checks),
        "checks": checks,
        "warning": "Soglie euristiche: descrivono la maturità della prova, non la qualità universale del modello.",
    }


def calculate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate accuracy, baselines, paired tests and independent fragility."""
    by_variant: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_variant[int(row["variant_id"])].append(row)

    variants = []
    for variant_id, variant_rows in by_variant.items():
        first = variant_rows[0]
        summary = _variant_summary(variant_rows)
        summary.update({
            "variant_id": variant_id,
            "variant_name": first.get("variant_name") or f"Variant {variant_id}",
            "question_id": int(first["question_id"]),
            "question_key": first.get("question_key") or "question",
            "language": first.get("language") or "und",
            "mutation_type": first.get("mutation_type") or "manual",
            "canonical": bool(first.get("canonical")),
        })
        variants.append(summary)
    variants.sort(key=lambda item: (item["question_id"], not item["canonical"], item["variant_name"].lower()))

    ground_truth_by_case: dict[tuple[int, int], str] = {}
    # Provenance is tracked for every annotated case, including the ones a panel
    # could not settle: those carry `uncertain` and drop out of accuracy, so
    # counting them here is the only place they are visible at all.
    provenance_by_case: dict[tuple[int, int], tuple[str, int]] = {}
    answers_by_case: dict[tuple[int, int], dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        case = (int(row["image_id"]), int(row["question_id"]))
        if row.get("ground_truth"):
            provenance_by_case[case] = (str(row.get("ground_truth_agreement") or "single"),
                                        int(row.get("ground_truth_labels") or 1))
        if row.get("ground_truth") in VERDICTS:
            ground_truth_by_case[case] = row["ground_truth"]
        if row.get("answer") in VERDICTS:
            answers_by_case[case][int(row["variant_id"])].append(row["answer"])

    ground_counts = Counter(ground_truth_by_case.values())
    majority = ground_counts.most_common(1)[0][0] if ground_counts else None
    majority_baseline = ground_counts[majority] / sum(ground_counts.values()) if majority else 0.0

    disagreeing = comparable = ties = 0
    fragile_cases = []
    for case, by_case_variant in answers_by_case.items():
        representative = {variant: _majority(values) for variant, values in by_case_variant.items()}
        representative = {variant: answer for variant, answer in representative.items() if answer in VERDICTS}
        ties += sum(1 for answer in by_case_variant if representative.get(answer) is None)
        local_disagree = local_total = 0
        for (_, first), (_, second) in combinations(representative.items(), 2):
            local_total += 1
            local_disagree += first != second
        disagreeing += local_disagree
        comparable += local_total
        if local_disagree:
            fragile_cases.append({
                "image_id": case[0], "question_id": case[1],
                "ground_truth": ground_truth_by_case.get(case), "answers": representative,
                "disagreements": local_disagree, "comparisons": local_total,
            })

    repeat_disagreeing = repeat_comparable = 0
    for by_case_variant in answers_by_case.values():
        for answers in by_case_variant.values():
            for first, second in combinations(answers, 2):
                repeat_comparable += 1
                repeat_disagreeing += first != second

    question_ids = sorted({int(row["question_id"]) for row in rows})
    questions, comparisons_out = [], []
    for question_id in question_ids:
        q_variants = [item for item in variants if item["question_id"] == question_id]
        accuracies = [item["accuracy"] for item in q_variants if item["parsed"]]
        canonical = next((item for item in q_variants if item["canonical"]), None)
        questions.append({
            "question_id": question_id,
            "question_key": q_variants[0]["question_key"] if q_variants else str(question_id),
            "variant_count": len(q_variants),
            "accuracy_min": min(accuracies) if accuracies else 0.0,
            "accuracy_max": max(accuracies) if accuracies else 0.0,
            "accuracy_spread": max(accuracies) - min(accuracies) if accuracies else 0.0,
        })
        if canonical:
            def representative_rows(variant_id: int) -> dict[int, dict[str, str]]:
                grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for row in by_variant[variant_id]:
                    if row.get("ground_truth") in VERDICTS and row.get("answer") in VERDICTS:
                        grouped[int(row["image_id"])].append(row)
                output: dict[int, dict[str, str]] = {}
                for image_id, image_rows in grouped.items():
                    answer = _majority(row["answer"] for row in image_rows)
                    if answer:
                        output[image_id] = {"answer": answer, "ground_truth": image_rows[0]["ground_truth"]}
                return output

            canonical_rows = representative_rows(canonical["variant_id"])
            for alternative in q_variants:
                if alternative["canonical"]:
                    continue
                alternative_rows = representative_rows(alternative["variant_id"])
                b = c = paired = 0
                for image_id in canonical_rows.keys() & alternative_rows.keys():
                    base_ok = canonical_rows[image_id]["answer"] == canonical_rows[image_id]["ground_truth"]
                    alt_ok = alternative_rows[image_id]["answer"] == alternative_rows[image_id]["ground_truth"]
                    paired += 1
                    if base_ok and not alt_ok:
                        b += 1
                    elif alt_ok and not base_ok:
                        c += 1
                comparisons_out.append({
                    "question_id": question_id, "canonical_variant_id": canonical["variant_id"],
                    "alternative_variant_id": alternative["variant_id"], "alternative_name": alternative["variant_name"],
                    "paired": paired, "canonical_only_correct": b, "alternative_only_correct": c,
                    "mcnemar_p": exact_mcnemar_p(b, c),
                })

    eligible_rows = [row for row in rows if row.get("ground_truth") in VERDICTS]
    parsed_rows = [row for row in eligible_rows if row.get("answer") in VERDICTS]
    correct = sum(row["answer"] == row["ground_truth"] for row in parsed_rows)
    # A verdict recovered from prose is weaker evidence than a schema-valid JSON
    # object. Splitting accuracy by parser shows how much of the headline number
    # rests on reading an answer the model never formally gave.
    by_parser: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in parsed_rows:
        by_parser[str(row.get("parser") or "none")].append(row)
    parser_breakdown = []
    for parser_name, parser_rows in sorted(by_parser.items()):
        hits = sum(row["answer"] == row["ground_truth"] for row in parser_rows)
        parser_low, parser_high = wilson_interval(hits, len(parser_rows))
        parser_breakdown.append({
            "parser": parser_name, "parsed": len(parser_rows), "correct": hits,
            "accuracy": hits / len(parser_rows), "ci_low": parser_low, "ci_high": parser_high,
            "share": len(parser_rows) / len(parsed_rows) if parsed_rows else 0.0,
        })
    strict_rows = by_parser.get("json", [])
    strict_correct = sum(row["answer"] == row["ground_truth"] for row in strict_rows)
    low, high = wilson_interval(correct, len(parsed_rows))
    latencies = [float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None]
    scene_groups: dict[str, list[bool]] = defaultdict(list)
    for row in parsed_rows:
        group = str(row.get("source_group") or f"image:{row['image_id']}")
        scene_groups[group].append(row["answer"] == row["ground_truth"])
    scene_balanced = statistics.mean(
        sum(group_results) / len(group_results) for group_results in scene_groups.values()
    ) if scene_groups else 0.0
    coverage = len(parsed_rows) / len(eligible_rows) if eligible_rows else 0.0
    format_rate = sum(bool(row.get("format_valid")) for row in eligible_rows) / len(eligible_rows) if eligible_rows else 0.0
    input_images = {int(row["image_id"]) for row in rows}
    normalized_inputs = {int(row["image_id"]) for row in rows
                         if row.get("input_preprocess") not in {None, "", "original", "failed"}}
    annotated_cases = len(provenance_by_case)
    verified_cases = sum(1 for _, count in provenance_by_case.values() if count >= 2)
    state_counts = Counter(state for state, _ in provenance_by_case.values())
    verified_share = verified_cases / annotated_cases if annotated_cases else 0.0
    gate = _evidence_gate(
        cases=len(ground_truth_by_case), independent_groups=len(scene_groups), questions=questions,
        coverage=coverage, format_rate=format_rate, comparisons=comparable,
        majority_baseline=majority_baseline, verified_share=verified_share,
    )
    fragile_cases.sort(key=lambda item: (-item["disagreements"], item["image_id"]))
    return {
        "summary": {
            "responses": len(rows), "evaluated": len(eligible_rows), "parsed": len(parsed_rows),
            "accuracy": correct / len(parsed_rows) if parsed_rows else 0.0,
            "scene_balanced_accuracy": scene_balanced, "independent_groups": len(scene_groups),
            "ground_truth_cases": len(ground_truth_by_case),
            "ci_low": low, "ci_high": high,
            "median_ms": statistics.median(latencies) if latencies else 0.0,
            "p95_ms": percentile(latencies, 0.95),
            "coverage": coverage, "format_rate": format_rate,
            "input_images": len(input_images), "normalized_inputs": len(normalized_inputs),
            "majority_baseline": majority_baseline, "majority_answer": majority,
            "annotated_cases": annotated_cases, "verified_cases": verified_cases,
            "verified_share": verified_share,
            "single_annotator_cases": state_counts.get("single", 0),
            "adjudicated_cases": state_counts.get("adjudicated", 0),
            # Unresolved disagreements never reach the accuracy figure. Saying how
            # many were dropped is the difference between a sample and a filter.
            "conflict_cases": state_counts.get("conflict", 0),
            "strict_parsed": len(strict_rows),
            "strict_accuracy": strict_correct / len(strict_rows) if strict_rows else 0.0,
            "strict_share": len(strict_rows) / len(parsed_rows) if parsed_rows else 0.0,
            "tie_units": ties,
            "prompt_fragility_score": 100 * disagreeing / comparable if comparable else 0.0,
            "prompt_comparisons": comparable,
            "repeat_instability_score": 100 * repeat_disagreeing / repeat_comparable if repeat_comparable else 0.0,
            "repeat_comparisons": repeat_comparable,
        },
        "evidence_gate": gate, "parser_breakdown": parser_breakdown,
        "variants": variants, "questions": questions,
        "comparisons": comparisons_out, "fragile_cases": fragile_cases[:100],
    }


def _arena_signature(run: dict[str, Any]) -> tuple[Any, ...]:
    """Properties that must match for a fair, paired model comparison."""
    config = run.get("config") or {}
    return (
        int(run["project_id"]),
        tuple(sorted(int(value) for value in config.get("question_ids") or [])),
        tuple(sorted(int(value) for value in config.get("variant_ids") or [])),
        int(config.get("repetitions", 1)),
        float(config.get("temperature", 0)),
        int(config.get("seed", 0)),
        int(config.get("max_tokens", 96)),
        str(config.get("split") or ""),
    )


def _arena_units(rows: list[dict[str, Any]]) -> dict[tuple[int, int], dict[str, Any]]:
    """Collapse repetitions to one correctness verdict per image/variant pair."""
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("ground_truth") in VERDICTS:
            grouped[(int(row["image_id"]), int(row["variant_id"]))].append(row)
    units: dict[tuple[int, int], dict[str, Any]] = {}
    for key, unit_rows in grouped.items():
        answer = _majority(row.get("answer") for row in unit_rows)
        if answer in VERDICTS:
            units[key] = {
                "correct": answer == unit_rows[0]["ground_truth"],
                "source_group": str(unit_rows[0].get("source_group") or f"image:{key[0]}"),
            }
    return units


def paired_difference_interval(differences: list[int], z: float = 1.959963984540054) -> tuple[float, float]:
    """Approximate 95% CI for the mean paired accuracy difference.

    Each observation is -1, 0 or 1. McNemar's exact test remains the
    inferential test; this interval communicates the magnitude and uncertainty.
    """
    if not differences:
        return (-1.0, 1.0)
    total = len(differences)
    if total == 1:
        return (-1.0, 1.0)
    b = sum(value == 1 for value in differences)
    c = sum(value == -1 for value in differences)
    mean = (b - c) / total
    # Without discordant pairs the observed standard error is exactly zero, and a
    # zero-width interval would claim a precision no finite sample can support.
    # The +1 is an Agresti-Min style continuity adjustment: mildly conservative
    # everywhere, and the difference between "0.0 → 0.0" and an honest band.
    variance = max(0.0, (b + c) - (b - c) ** 2 / total)
    standard_error = math.sqrt(variance + 1) / total
    return (max(-1.0, mean - z * standard_error), min(1.0, mean + z * standard_error))


def calculate_arena(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare two or more completed, configuration-compatible model runs.

    Every ranked number is computed on the units that *all* models answered
    readably. Ranking each model on its own surviving units would let the model
    that gives up on the hard images win by shrinking its own sample; coverage
    reports separately how much each model skipped.
    """
    if not 2 <= len(entries) <= 12:
        raise ValueError("Seleziona da 2 a 12 esecuzioni completate")
    signatures = {_arena_signature(entry["run"]) for entry in entries}
    if len(signatures) != 1:
        raise ValueError(
            "Le esecuzioni non sono compatibili: progetto, domande, varianti, "
            "ripetizioni, temperatura, seed e token massimi devono coincidere"
        )
    demo_values = {bool(entry["run"].get("provider_is_demo")) for entry in entries}
    if len(demo_values) != 1:
        raise ValueError("Non confrontare run DEMO con run di modelli reali")
    for entry in entries:
        if entry["run"].get("status") != "completed":
            raise ValueError("La Model Arena accetta soltanto esecuzioni completate")

    unit_maps = {int(entry["run"]["id"]): _arena_units(entry["rows"]) for entry in entries}
    common_keys = set.intersection(*(set(units) for units in unit_maps.values()))
    union_keys = set.union(*(set(units) for units in unit_maps.values()))
    if not common_keys:
        raise ValueError(
            "Nessuna unità comune: i modelli selezionati non hanno prodotto un verdetto "
            "leggibile sulle stesse immagini, quindi non esiste un confronto appaiato da mostrare"
        )

    ranked: list[dict[str, Any]] = []
    for entry in entries:
        run, rows = entry["run"], entry["rows"]
        run_id = int(run["id"])
        units = unit_maps[run_id]
        matched_rows = [row for row in rows
                        if (int(row["image_id"]), int(row["variant_id"])) in common_keys]
        summary = calculate_metrics(matched_rows)["summary"]
        matched_correct = sum(bool(units[key]["correct"]) for key in common_keys)
        accuracy_low, accuracy_high = wilson_interval(matched_correct, len(common_keys))
        scene_units: dict[str, list[bool]] = defaultdict(list)
        for key in common_keys:
            scene_units[units[key]["source_group"]].append(bool(units[key]["correct"]))
        scene_balanced = statistics.mean(
            sum(values) / len(values) for values in scene_units.values()
        ) if scene_units else 0.0
        eligible_units = {(int(row["image_id"]), int(row["variant_id"])) for row in rows
                          if row.get("ground_truth") in VERDICTS}
        own_correct = sum(bool(unit["correct"]) for unit in units.values())
        # Wall-clock across a pause would charge a model for the hours it slept.
        elapsed = float(run.get("runtime_seconds") or 0) or max(
            0.0, float(run.get("finished_at") or 0) - float(run.get("started_at") or 0))
        ranked.append({
            "run_id": run_id,
            "run_name": run.get("name") or f"Run {run_id}",
            "provider_name": run.get("provider_name") or run.get("provider_model") or "Modello",
            "provider_model": run.get("provider_model") or "",
            "is_demo": bool(run.get("provider_is_demo")),
            "accuracy": matched_correct / len(common_keys),
            "ci_low": accuracy_low, "ci_high": accuracy_high,
            "scene_balanced_accuracy": scene_balanced,
            "matched_units": len(common_keys), "matched_correct": matched_correct,
            "own_accuracy": own_correct / len(units) if units else 0.0,
            "own_units": len(units),
            "coverage": len(units) / len(eligible_units) if eligible_units else 0.0,
            "format_rate": summary["format_rate"],
            "strict_share": summary["strict_share"],
            "prompt_fragility_score": summary["prompt_fragility_score"],
            "repeat_instability_score": summary["repeat_instability_score"],
            "median_ms": summary["median_ms"], "p95_ms": summary["p95_ms"],
            "evaluated_units": len(units), "elapsed_seconds": elapsed,
            "responses_per_second": float(run.get("completed") or 0) / elapsed if elapsed else 0.0,
        })
    ranked.sort(key=lambda item: (
        -item["accuracy"], -item["scene_balanced_accuracy"], item["prompt_fragility_score"],
        item["repeat_instability_score"], item["median_ms"], item["run_id"],
    ))
    for index, item in enumerate(ranked, 1):
        item["rank"] = index

    pairwise = []
    for first, second in combinations(ranked, 2):
        first_units, second_units = unit_maps[first["run_id"]], unit_maps[second["run_id"]]
        shared = sorted(first_units.keys() & second_units.keys())
        differences = [int(first_units[key]["correct"]) - int(second_units[key]["correct"]) for key in shared]
        first_wins = sum(value == 1 for value in differences)
        second_wins = sum(value == -1 for value in differences)
        both_correct = sum(first_units[key]["correct"] and second_units[key]["correct"] for key in shared)
        both_wrong = sum(not first_units[key]["correct"] and not second_units[key]["correct"] for key in shared)
        delta = statistics.mean(differences) if differences else 0.0
        ci_low, ci_high = paired_difference_interval(differences)
        pairwise.append({
            "run_a_id": first["run_id"], "run_b_id": second["run_id"],
            "run_a_name": first["provider_name"], "run_b_name": second["provider_name"],
            "run_a_model": first["provider_model"], "run_b_model": second["provider_model"],
            "paired": len(shared), "a_only_correct": first_wins, "b_only_correct": second_wins,
            "both_correct": both_correct, "both_wrong": both_wrong,
            "accuracy_delta": delta, "delta_ci_low": ci_low, "delta_ci_high": ci_high,
            "mcnemar_p": exact_mcnemar_p(first_wins, second_wins),
        })

    def sole_leader(key: Any) -> int | None:
        """No badge on a tie: crowning an arbitrary winner among equals is the
        false confidence this tool exists to expose."""
        scored = sorted(ranked, key=key)
        best = key(scored[0])
        return scored[0]["run_id"] if len(scored) == 1 or key(scored[1]) != best else None

    fastest = sole_leader(lambda item: item["median_ms"] or math.inf)
    most_robust = sole_leader(lambda item: (item["prompt_fragility_score"], item["repeat_instability_score"]))
    most_accurate = sole_leader(lambda item: (-item["accuracy"], -item["scene_balanced_accuracy"]))
    weakest_coverage = min(ranked, key=lambda item: item["coverage"])
    signature = next(iter(signatures))
    overlap_rate = len(common_keys) / len(union_keys) if union_keys else 0.0
    warning = ("La classifica vale soltanto per questo dataset, queste annotazioni e questa "
               "configurazione, ed è calcolata sulle sole unità che tutti i modelli hanno "
               "saputo giudicare.")
    if overlap_rate < 1:
        warning += (f" {pct_text(1 - overlap_rate)} delle unità è stato escluso perché almeno un "
                    f"modello non vi ha risposto in modo leggibile: la copertura più bassa è "
                    f"{pct_text(weakest_coverage['coverage'])} ({weakest_coverage['provider_name']}), "
                    "e una copertura bassa è essa stessa un risultato da riportare.")
    return {
        "ranking_basis": "accuracy sulle unità comuni, poi scene-balanced accuracy, fragilità, repeat drift e latenza",
        "interval_method": "Wilson 95% per accuratezza; approssimazione normale appaiata 95% per le differenze; McNemar esatto per p",
        "compatibility": {
            "project_id": signature[0], "question_ids": list(signature[1]), "variant_ids": list(signature[2]),
            "repetitions": signature[3], "temperature": signature[4], "seed": signature[5],
            "max_tokens": signature[6], "split": signature[7],
            "common_units": len(common_keys), "union_units": len(union_keys),
            "overlap_rate": overlap_rate, "fully_matched": len(common_keys) == len(union_keys),
        },
        "leaders": {"accuracy": most_accurate, "speed": fastest, "robustness": most_robust},
        "is_demo": bool(next(iter(demo_values))),
        "models": ranked, "pairwise": pairwise,
        "warning": warning,
    }
