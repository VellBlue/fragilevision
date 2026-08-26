"""Self-contained human and machine-readable run artifacts."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
import math
from typing import Any


def pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def pct_or_dash(value: float | None) -> str:
    """Balanced accuracy is undefined with a single ground-truth class."""
    return "—" if value is None else pct(value)


def coefficient(value: float | None) -> str:
    """A coefficient nobody could compute is a dash, never a zero."""
    return "—" if value is None else f"{value:.3f}"


def bar_text(value: float, *, width: int = 16) -> str:
    """A block-character bar for plain-text viewers that render no images at all."""
    filled = round(width * max(0.0, min(1.0, value)))
    return "█" * filled + "░" * (width - filled)


def svg_score_dial(score: float, *, label: str = "PFS / 100") -> str:
    """A radial gauge matching the live Failure Atlas's own dial, so the exported
    report reads as the same artifact rather than a second, unrelated summary."""
    clamped = max(0.0, min(100.0, score))
    circumference = 2 * math.pi * 50
    offset = circumference * (1 - clamped / 100)
    return f"""<svg class="score-dial" viewBox="0 0 120 120" width="140" height="140" role="img"
aria-label="{escape(label)}: {clamped:.1f}">
<circle cx="60" cy="60" r="50" fill="none" style="stroke:var(--line)" stroke-width="8"/>
<circle cx="60" cy="60" r="50" fill="none" style="stroke:var(--acid)" stroke-width="8" stroke-linecap="round"
stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{offset:.2f}" transform="rotate(-90 60 60)"/>
<text x="60" y="57" text-anchor="middle" font-size="26" font-weight="800" style="fill:var(--ink)">{clamped:.1f}</text>
<text x="60" y="75" text-anchor="middle" font-size="8" style="fill:var(--muted)" letter-spacing="1">{escape(label.upper())}</text>
</svg>"""


def svg_variant_bars(variants: list[dict[str, Any]], baseline: float, *, width: int = 640) -> str:
    """One horizontal bar per variant, with the majority baseline marked — the
    same picture the live UI's Variant Fingerprint panel shows."""
    if not variants:
        return '<p class="label">No comparable responses.</p>'
    row_height, label_width, value_width = 30, 230, 54
    track_width = width - label_width - value_width
    height = row_height * len(variants) + 6
    baseline_x = label_width + track_width * max(0.0, min(1.0, baseline))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" '
             f'aria-label="Variant accuracy, baseline {pct(baseline)}">']
    for index, item in enumerate(variants):
        y = index * row_height + 4
        bar_w = track_width * max(0.0, min(1.0, item["accuracy"]))
        name = escape(f"{item['question_key']} / {item['variant_name']}"[:34])
        parts.append(
            f'<text x="0" y="{y + 14}" font-size="11" style="fill:var(--ink)">{name}</text>'
            f'<rect x="{label_width}" y="{y}" width="{track_width}" height="18" rx="4" '
            f'fill="none" style="stroke:var(--line)"/>'
            f'<rect x="{label_width}" y="{y}" width="{bar_w:.1f}" height="18" rx="4" style="fill:var(--blue)"/>'
            f'<line x1="{baseline_x:.1f}" y1="{y - 3}" x2="{baseline_x:.1f}" y2="{y + 21}" '
            f'style="stroke:var(--coral)" stroke-width="2"/>'
            f'<text x="{label_width + track_width + 8}" y="{y + 14}" font-size="11" '
            f'style="fill:var(--muted)">{pct(item["accuracy"])}</text>')
    parts.append("</svg>")
    return "".join(parts)


def build_agreement_section(agreement: dict[str, Any] | None, summary: dict[str, Any]) -> str:
    """How the ground truth was produced, and by how many people."""
    verified = summary.get("verified_share", 0.0)
    conflicts = summary.get("conflict_cases", 0)
    if not agreement:
        return ("<h2>Ground truth</h2><p class=\"warning\">No inter-annotator agreement report available "
                "for this run.</p>")
    overall = agreement.get("overall") or {}
    reviewers = agreement.get("annotator_count", 0)
    rows = "".join(
        "<tr>"
        f"<td>{escape(item['a'])} ↔ {escape(item['b'])}</td><td>{item['shared']}</td>"
        f"<td>{pct(item['percent_agreement']) if item['percent_agreement'] is not None else '—'}</td>"
        f"<td>{coefficient(item['cohen_kappa'])}</td>"
        f"<td>{coefficient(item['kappa_ci_low'])}–{coefficient(item['kappa_ci_high'])}</td>"
        f"<td>{escape(item['kappa_label'])}</td></tr>" for item in agreement.get("pairs") or []
    ) or '<tr><td colspan="6">A single reviewer: no pair to compare</td></tr>'
    warnings = "".join(f"<li class=\"fail\">{escape(item['text'])}</li>"
                       for item in (agreement.get("warnings") or [])[:6])
    alone = ('<p class="demo-warning">SINGLE-ANNOTATOR GROUND TRUTH — this claim cannot distinguish the '
             'labels from the habits of the one person who wrote them.</p>' if reviewers < 2 else "")
    return f"""<h2>Ground truth reliability</h2>{alone}
<div class="grid">
<div class="card"><div class="big">{reviewers}</div><div class="label">Reviewers</div></div>
<div class="card"><div class="big">{coefficient(overall.get('krippendorff_alpha'))}</div>
<div class="label">Krippendorff α · {escape(overall.get('alpha_label', '—'))}</div></div>
<div class="card"><div class="big">{pct(verified)}</div><div class="label">Cases judged more than once</div></div>
<div class="card"><div class="big">{conflicts}</div><div class="label">Unresolved conflicts, excluded</div></div>
</div>
<p>95% bootstrap interval on α: <strong>{coefficient(overall.get('alpha_ci_low'))}–{coefficient(overall.get('alpha_ci_high'))}</strong>
over {overall.get('reliability_units', 0)} doubly annotated case(s). Observed pairwise agreement:
<strong>{pct(overall['percent_agreement']) if overall.get('percent_agreement') is not None else '—'}</strong>.
Fleiss' κ: <strong>{coefficient(overall.get('fleiss_kappa'))}</strong> (defined only for a constant panel).</p>
<table><thead><tr><th>Pair</th><th>Shared cases</th><th>Agreement</th><th>Cohen κ</th><th>95% bootstrap</th>
<th>Reading</th></tr></thead><tbody>{rows}</tbody></table>
<ul>{warnings}</ul>
<p class="label">{escape(agreement.get('limitations', ''))}</p>"""


def build_report(run: dict[str, Any], metrics: dict[str, Any], fingerprint: str,
                 agreement: dict[str, Any] | None = None) -> str:
    summary = metrics["summary"]
    gate = metrics.get("evidence_gate") or {"grade": "E", "status": "insufficient", "checks": [], "warning": ""}
    rows = "".join(
        "<tr>"
        f"<td>{escape(item['question_key'])}</td><td>{escape(item['variant_name'])}</td>"
        f"<td>{escape(item['language'])}</td><td>{pct(item['accuracy'])}</td>"
        f"<td>{pct_or_dash(item['balanced_accuracy'])}</td><td>{pct(item['coverage'])}</td>"
        f"<td>{pct(item['format_rate'])}</td><td>{item['median_ms']:.0f} ms</td>"
        "</tr>" for item in metrics["variants"]
    ) or '<tr><td colspan="8">No comparable responses</td></tr>'
    comparisons = "".join(
        "<tr>"
        f"<td>{escape(item['alternative_name'])}</td><td>{item['paired']}</td>"
        f"<td>{item['canonical_only_correct']}</td><td>{item['alternative_only_correct']}</td>"
        f"<td>{item['mcnemar_p']:.4g}</td></tr>" for item in metrics["comparisons"]
    ) or '<tr><td colspan="5">No paired alternatives</td></tr>'
    generated = datetime.now(timezone.utc).isoformat()
    gate_rows = "".join(
        f"<li class=\"{'pass' if item['passed'] else 'fail'}\">{'✓' if item['passed'] else '×'} "
        f"{escape(item['label'])} <code>{escape(str(item.get('display', item['value'])))}</code></li>" for item in gate["checks"]
    )
    parser_rows = "".join(
        "<tr>"
        f"<td>{escape(item['parser'])}</td><td>{item['parsed']}</td><td>{pct(item['share'])}</td>"
        f"<td>{pct(item['accuracy'])}</td><td>{pct(item['ci_low'])}–{pct(item['ci_high'])}</td>"
        "</tr>" for item in metrics.get("parser_breakdown") or []
    ) or '<tr><td colspan="5">No parsed verdicts</td></tr>'
    demo_warning = ('<p class="demo-warning">SYNTHETIC DEMO RUN — non è il risultato di un modello visivo reale.</p>'
                    if run.get("provider_is_demo") else "")
    embedded = json.dumps({"run": run, "metrics": metrics, "fingerprint": fingerprint,
                           "agreement": agreement}, ensure_ascii=False).replace("</", "<\\/")
    variant_chart = svg_variant_bars(metrics["variants"], summary.get("majority_baseline", 0.0))
    score_dial = svg_score_dial(summary["prompt_fragility_score"])
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FragileVision · {escape(run['name'])}</title>
<style>
:root{{--ink:#f5f1e8;--muted:#a7a39a;--bg:#0d0f10;--panel:#171a1c;--line:#2b3033;
--acid:#c8ff56;--coral:#ff766c;--blue:#74b8ff}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);
color:var(--ink);font:15px/1.55 ui-sans-serif,system-ui;padding:40px}}main{{max-width:1100px;margin:auto}}
h1{{font-size:42px;line-height:1;margin:.2em 0}}.kicker{{color:var(--acid);letter-spacing:.16em;text-transform:uppercase}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:28px 0}}.card{{background:var(--panel);
border:1px solid var(--line);border-radius:16px;padding:18px}}.big{{font-size:30px;font-weight:750}}.label{{color:var(--muted)}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border-radius:14px;overflow:hidden;margin:12px 0 30px}}
th,td{{text-align:left;padding:11px;border-bottom:1px solid var(--line)}}th{{color:var(--muted);font-size:12px}}
code{{color:var(--blue)}}.warning,.fail{{color:var(--coral)}}.pass{{color:var(--acid)}}.gate{{display:grid;grid-template-columns:100px 1fr;gap:22px;align-items:start}}.grade{{font-size:58px;font-weight:850;text-align:center;border:1px solid var(--line);border-radius:18px;padding:12px}}ul{{list-style:none;padding:0;margin:0}}li{{padding:7px 0;border-bottom:1px solid var(--line)}}li code{{float:right}}.demo-warning{{border:1px solid var(--coral);color:var(--coral);padding:12px;border-radius:10px;font-weight:800}}
.topline{{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap}}
.print-button{{background:var(--acid);color:#11140f;border:0;border-radius:10px;padding:11px 18px;font-weight:700;cursor:pointer;font-size:14px}}
.chart-card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin:18px 0}}
.dial-row{{display:flex;gap:26px;align-items:center;flex-wrap:wrap}}
@media(max-width:760px){{body{{padding:20px}}.grid{{grid-template-columns:1fr 1fr}}}}
@media print{{:root{{--ink:#111;--muted:#555;--bg:#fff;--panel:#f6f6f3;--line:#ccc;--acid:#2f6f2f;--coral:#a33;--blue:#255a9c}}
body{{padding:16px}}.no-print{{display:none!important}}.card,table,.gate,.chart-card,tr{{break-inside:avoid}}
a{{color:inherit;text-decoration:none}}}}
</style><main>
<div class="topline"><div><div class="kicker">FragileVision claim card</div><h1>{escape(run['name'])}</h1>
<p>{escape(run.get('project_name',''))} · {escape(run.get('provider_model',''))}</p></div>
<button class="print-button no-print" onclick="window.print()">Esporta PDF (stampa dal browser)</button></div>
{demo_warning}
<div class="grid">
<div class="card"><div class="big">{summary['prompt_fragility_score']:.1f}</div><div class="label">Prompt Fragility / 100</div></div>
<div class="card"><div class="big">{pct(summary['accuracy'])}</div><div class="label">Accuracy</div></div>
<div class="card"><div class="big">{pct(summary['majority_baseline'])}</div><div class="label">Majority baseline</div></div>
<div class="card"><div class="big">{pct(summary['coverage'])}</div><div class="label">Readable verdicts</div></div>
</div>
<p>95% Wilson interval: <strong>{pct(summary['ci_low'])}–{pct(summary['ci_high'])}</strong>.
Repeat instability: <strong>{summary['repeat_instability_score']:.1f}/100</strong>. Scene-balanced accuracy:
<strong>{pct(summary.get('scene_balanced_accuracy', 0))}</strong> over {summary.get('independent_groups', 0)} independent groups.</p>
<div class="chart-card dial-row">{score_dial}<div><h3 style="margin:0 0 6px">Prompt Fragility Score</h3>
<p class="label" style="margin:0">Share of paired verdicts that flipped between a canonical question and its controlled
mutations. 0 means every rewording got the same answer; 100 means none did.</p></div></div>
<h2>Evidence Gate <small>· {escape(gate['status'])}</small></h2><div class="gate"><div class="grade">{escape(gate['grade'])}</div><div><ul>{gate_rows}</ul><p class="label">{escape(gate['warning'])}</p></div></div>
<h2>Variant Fingerprint</h2><div class="chart-card">{variant_chart}</div>
<h2>Variants</h2><table><thead><tr><th>Question</th><th>Variant</th><th>Language</th><th>Accuracy</th>
<th>Balanced</th><th>Coverage</th><th>Format</th><th>Median</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Paired evidence</h2><table><thead><tr><th>Alternative</th><th>Pairs</th><th>Canonical only</th>
<th>Alternative only</th><th>Exact McNemar p</th></tr></thead><tbody>{comparisons}</tbody></table>
<h2>Parser honesty</h2><p>A verdict recovered from prose is weaker evidence than a schema-valid JSON
object. <strong>{pct(summary.get('strict_share', 0))}</strong> of the counted verdicts arrived as strict JSON.
{summary.get('tie_units', 0)} unit(s) were discarded because repeated calls split evenly and the model
committed to nothing.</p>
<table><thead><tr><th>Parser</th><th>Verdicts</th><th>Share</th><th>Accuracy</th><th>95% Wilson</th></tr></thead>
<tbody>{parser_rows}</tbody></table>
{build_agreement_section(agreement, summary)}
<h2>Scope</h2><p>This claim applies only to the recorded dataset, prompts, model revision and inference configuration.
It is not a universal model ranking.</p><p>Evaluation fingerprint: <code>{fingerprint}</code><br>Generated: {generated}</p>
<script type="application/json" id="fragilevision-data">{embedded}</script></main></html>"""


def md_cell(value: Any) -> str:
    """Escape a value for a GitHub-flavored Markdown table cell: no pipes, no newlines."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def build_agreement_section_markdown(agreement: dict[str, Any] | None, summary: dict[str, Any]) -> str:
    if not agreement:
        return "## Ground truth reliability\n\nNo inter-annotator agreement report available for this run.\n"
    overall = agreement.get("overall") or {}
    reviewers = agreement.get("annotator_count", 0)
    lines = ["## Ground truth reliability\n"]
    if reviewers < 2:
        lines.append("> **SINGLE-ANNOTATOR GROUND TRUTH** — this claim cannot distinguish the labels from "
                     "the habits of the one person who wrote them.\n")
    lines.append(
        f"- Reviewers: **{reviewers}**\n"
        f"- Krippendorff's α: **{coefficient(overall.get('krippendorff_alpha'))}** "
        f"({md_cell(overall.get('alpha_label', '—'))}), 95% bootstrap "
        f"{coefficient(overall.get('alpha_ci_low'))}–{coefficient(overall.get('alpha_ci_high'))} "
        f"over {overall.get('reliability_units', 0)} doubly annotated case(s)\n"
        f"- Observed pairwise agreement: "
        f"**{pct(overall['percent_agreement']) if overall.get('percent_agreement') is not None else '—'}**\n"
        f"- Fleiss' κ: **{coefficient(overall.get('fleiss_kappa'))}** (defined only for a constant panel)\n"
        f"- Cases judged by more than one reviewer: **{pct(summary.get('verified_share', 0.0))}**\n"
        f"- Unresolved conflicts, excluded from accuracy: **{summary.get('conflict_cases', 0)}**\n"
    )
    pairs = agreement.get("pairs") or []
    if pairs:
        lines.append("\n| Pair | Shared cases | Agreement | Cohen κ | 95% bootstrap | Reading |")
        lines.append("|---|---|---|---|---|---|")
        for item in pairs:
            agreement_pct = pct(item["percent_agreement"]) if item["percent_agreement"] is not None else "—"
            lines.append(f"| {md_cell(item['a'])} ↔ {md_cell(item['b'])} | {item['shared']} | {agreement_pct} | "
                        f"{coefficient(item['cohen_kappa'])} | "
                        f"{coefficient(item['kappa_ci_low'])}–{coefficient(item['kappa_ci_high'])} | "
                        f"{md_cell(item['kappa_label'])} |")
    warnings = agreement.get("warnings") or []
    if warnings:
        lines.append("\n" + "\n".join(f"- ⚠️ {md_cell(item['text'])}" for item in warnings[:6]))
    lines.append(f"\n> {md_cell(agreement.get('limitations', ''))}\n")
    return "\n".join(lines)


def build_report_markdown(run: dict[str, Any], metrics: dict[str, Any], fingerprint: str,
                          agreement: dict[str, Any] | None = None) -> str:
    """A publishable report for a repo README, a wiki page or a paste into an
    issue — plain GitHub-flavored Markdown, readable with no image renderer at
    all. Charts belong to the HTML Claim Card; here a block-character bar
    stands in, since it survives copy-paste and renders in a bare text view."""
    summary = metrics["summary"]
    gate = metrics.get("evidence_gate") or {"grade": "E", "status": "insufficient", "checks": [], "warning": ""}
    generated = datetime.now(timezone.utc).isoformat()
    demo_note = ("\n> **SYNTHETIC DEMO RUN** — non è il risultato di un modello visivo reale.\n"
                if run.get("provider_is_demo") else "")

    lines = [
        f"# FragileVision Claim Card — {md_cell(run['name'])}\n",
        f"**Project:** {md_cell(run.get('project_name', ''))} · "
        f"**Model:** {md_cell(run.get('provider_model', ''))} ({md_cell(run.get('provider_kind', ''))})  ",
        f"**Generated:** {generated} · **Fingerprint:** `{fingerprint}`\n",
        demo_note,
        "## Summary\n",
        "| Metric | Value |",
        "|---|---|",
        f"| Prompt Fragility Score | {summary['prompt_fragility_score']:.1f}/100 |",
        f"| Accuracy | {pct(summary['accuracy'])} (95% Wilson {pct(summary['ci_low'])}–{pct(summary['ci_high'])}) |",
        f"| Majority baseline | {pct(summary['majority_baseline'])} |",
        f"| Readable verdicts (coverage) | {pct(summary['coverage'])} |",
        f"| Repeat instability | {summary['repeat_instability_score']:.1f}/100 |",
        f"| Scene-balanced accuracy | {pct(summary.get('scene_balanced_accuracy', 0))} over "
        f"{summary.get('independent_groups', 0)} independent group(s) |",
        "",
        f"## Evidence Gate — Grade {md_cell(gate['grade'])} ({md_cell(gate['status'])})\n",
    ]
    for item in gate["checks"]:
        mark = "x" if item["passed"] else " "
        lines.append(f"- [{mark}] {md_cell(item['label'])} — `{md_cell(item.get('display', item['value']))}`")
    lines.append(f"\n> {md_cell(gate['warning'])}\n")

    lines.append("## Variant Fingerprint\n")
    lines.append("```")
    for item in metrics["variants"]:
        name = f"{item['question_key']} / {item['variant_name']}"[:40]
        lines.append(f"{name:<42} {bar_text(item['accuracy'])} {pct(item['accuracy'])}")
    lines.append("```\n")

    lines.append("## Variants\n")
    lines.append("| Question | Variant | Language | Accuracy | Balanced | Coverage | Format | Median |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for item in metrics["variants"]:
        lines.append(f"| {md_cell(item['question_key'])} | {md_cell(item['variant_name'])} | "
                     f"{md_cell(item['language'])} | {pct(item['accuracy'])} | "
                     f"{pct_or_dash(item['balanced_accuracy'])} | {pct(item['coverage'])} | "
                     f"{pct(item['format_rate'])} | {item['median_ms']:.0f} ms |")
    if not metrics["variants"]:
        lines.append("| _no comparable responses_ | | | | | | | |")

    lines.append("\n## Paired evidence (exact McNemar)\n")
    lines.append("| Alternative | Pairs | Canonical only | Alternative only | p |")
    lines.append("|---|---|---|---|---|")
    for item in metrics["comparisons"]:
        lines.append(f"| {md_cell(item['alternative_name'])} | {item['paired']} | "
                     f"{item['canonical_only_correct']} | {item['alternative_only_correct']} | "
                     f"{item['mcnemar_p']:.4g} |")
    if not metrics["comparisons"]:
        lines.append("| _no paired alternatives_ | | | | |")

    lines.append("\n## Parser honesty\n")
    lines.append(f"A verdict recovered from prose is weaker evidence than a schema-valid JSON object. "
                f"**{pct(summary.get('strict_share', 0))}** of the counted verdicts arrived as strict JSON. "
                f"{summary.get('tie_units', 0)} unit(s) were discarded because repeated calls split evenly "
                f"and the model committed to nothing.\n")
    lines.append("| Parser | Verdicts | Share | Accuracy | 95% Wilson |")
    lines.append("|---|---|---|---|---|")
    for item in metrics.get("parser_breakdown") or []:
        lines.append(f"| {md_cell(item['parser'])} | {item['parsed']} | {pct(item['share'])} | "
                     f"{pct(item['accuracy'])} | {pct(item['ci_low'])}–{pct(item['ci_high'])} |")
    if not metrics.get("parser_breakdown"):
        lines.append("| _no parsed verdicts_ | | | | |")

    lines.append("")
    lines.append(build_agreement_section_markdown(agreement, summary))

    lines.append("## Scope\n")
    lines.append("This claim applies only to the recorded dataset, prompts, model revision and inference "
                "configuration. It is not a universal model ranking.\n")
    return "\n".join(lines) + "\n"


def build_eval_yaml(run: dict[str, Any], fingerprint: str) -> str:
    config = run.get("config") or {}
    question_ids = ", ".join(str(value) for value in config.get("question_ids", []))
    return (
        "# FragileVision reproducibility manifest\n"
        "schema: fragilevision/eval@1\n"
        f"name: {json.dumps(run['name'], ensure_ascii=False)}\n"
        f"model: {json.dumps(run.get('provider_model',''), ensure_ascii=False)}\n"
        f"provider: {json.dumps(run.get('provider_kind',''), ensure_ascii=False)}\n"
        f"evaluation_fingerprint: {fingerprint}\n"
        f"question_ids: [{question_ids}]\n"
        f"dataset_split: {json.dumps(config.get('split') or 'tutto il dataset', ensure_ascii=False)}\n"
        f"repetitions: {int(config.get('repetitions', 1))}\n"
        f"temperature: {float(config.get('temperature', 0))}\n"
        f"seed: {int(config.get('seed', 0))}\n"
        "answer_schema: [yes, no, uncertain]\n"
        "ground_truth_consensus: majority-of-independent-labels, ties left unresolved and excluded\n"
        "metrics: [accuracy, balanced_accuracy, scene_balanced_accuracy, majority_baseline, prompt_fragility, repeat_instability, exact_mcnemar, evidence_gate, krippendorff_alpha, cohen_kappa]\n"
    )
