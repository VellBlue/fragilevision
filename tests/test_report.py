from __future__ import annotations

import unittest

from fragilevision.metrics import calculate_metrics
from fragilevision.report import (bar_text, build_agreement_section_markdown, build_report,
                                  build_report_markdown, md_cell, svg_score_dial, svg_variant_bars)


def sample_metrics():
    rows = []
    for image_id in range(1, 11):
        truth = "yes" if image_id % 2 else "no"
        for variant_id, name, canonical in [(1, "Canonica", True), (2, "Negazione", False)]:
            answer = truth if image_id != 3 or variant_id == 1 else "no" if truth == "yes" else "yes"
            rows.append({"variant_id": variant_id, "variant_name": name, "question_id": 1,
                        "question_key": "luce", "language": "it", "canonical": canonical,
                        "image_id": image_id, "answer": answer, "ground_truth": truth,
                        "format_valid": True, "parser": "json", "latency_ms": 900,
                        "source_group": f"scena-{image_id % 3}"})
    return calculate_metrics(rows)


SAMPLE_RUN = {"name": "Prova | con pipe\ne newline", "project_name": "Progetto", "provider_model": "qwen3-vl:8b",
             "provider_kind": "ollama", "provider_is_demo": 0,
             "config": {"question_ids": [1], "repetitions": 1, "temperature": 0, "seed": 0, "split": ""}}


class HelperTests(unittest.TestCase):
    def test_bar_text_is_proportional_and_bounded(self):
        self.assertEqual(bar_text(0.0, width=10), "░" * 10)
        self.assertEqual(bar_text(1.0, width=10), "█" * 10)
        self.assertEqual(bar_text(0.5, width=10), "█████░░░░░")
        # Out-of-range inputs are clamped, never overflow the requested width.
        self.assertEqual(bar_text(-1.0, width=10), "░" * 10)
        self.assertEqual(bar_text(2.0, width=10), "█" * 10)

    def test_md_cell_escapes_table_breaking_characters(self):
        self.assertEqual(md_cell("a | b\nc"), "a \\| b c")

    def test_svg_score_dial_clamps_out_of_range_scores(self):
        self.assertIn('aria-label="PFS / 100: 100.0"', svg_score_dial(999))
        self.assertIn('aria-label="PFS / 100: 0.0"', svg_score_dial(-50))

    def test_svg_variant_bars_handles_the_empty_case(self):
        self.assertIn("No comparable", svg_variant_bars([], 0.5))

    def test_agreement_markdown_flags_a_lone_reviewer(self):
        section = build_agreement_section_markdown(
            {"annotator_count": 1, "overall": {}, "pairs": [], "warnings": [], "limitations": "x"}, {})
        self.assertIn("SINGLE-ANNOTATOR", section)

    def test_agreement_markdown_handles_no_report_at_all(self):
        section = build_agreement_section_markdown(None, {})
        self.assertIn("No inter-annotator agreement report", section)


class ReportContentTests(unittest.TestCase):
    def setUp(self):
        self.metrics = sample_metrics()

    def test_html_report_embeds_both_charts_and_survives_a_name_with_markup(self):
        run = SAMPLE_RUN | {"name": "<script>alert(1)</script>"}
        html = build_report(run, self.metrics, "fingerprint123")
        self.assertIn("<svg class=\"score-dial\"", html)
        self.assertIn("luce / Canonica", html)
        self.assertNotIn("<script>alert(1)</script>", html)  # must be escaped, not executed
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("window.print()", html)
        self.assertIn("@media print", html)

    def test_html_report_flags_a_demo_run(self):
        html = build_report(SAMPLE_RUN | {"provider_is_demo": 1}, self.metrics, "fp")
        self.assertIn("SYNTHETIC DEMO RUN", html)

    def test_markdown_report_is_valid_gfm_shaped_text_and_escapes_the_run_name(self):
        md = build_report_markdown(SAMPLE_RUN, self.metrics, "fingerprint123")
        self.assertIn("# FragileVision Claim Card", md)
        # The run name contains a literal pipe and a newline: both would break
        # a Markdown table if not escaped, so the title line must not either.
        self.assertNotIn("pipe\ne newline", md)
        self.assertIn("| Metric | Value |", md)
        self.assertIn("luce / Canonica", md)
        self.assertIn("```", md)  # the block-character bar chart fence

    def test_markdown_and_html_reports_agree_on_the_headline_numbers(self):
        html = build_report(SAMPLE_RUN, self.metrics, "fp")
        md = build_report_markdown(SAMPLE_RUN, self.metrics, "fp")
        score = f"{self.metrics['summary']['prompt_fragility_score']:.1f}"
        self.assertIn(score, html)
        self.assertIn(score, md)

    def test_markdown_report_never_raises_on_an_empty_run(self):
        empty_metrics = calculate_metrics([])
        md = build_report_markdown(SAMPLE_RUN, empty_metrics, "fp")
        self.assertIn("no comparable responses", md)
        self.assertIn("no paired alternatives", md)


if __name__ == "__main__":
    unittest.main()
