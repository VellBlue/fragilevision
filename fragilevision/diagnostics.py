"""Deterministic, local failure-pattern diagnosis."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .metrics import VERDICTS, wilson_interval


LABELS = {
    "dark":"immagini scure", "bright":"immagini molto luminose", "low_contrast":"basso contrasto",
    "fine_detail":"dettaglio fine o texture dense", "small_image":"risoluzione ridotta",
    "portrait":"inquadratura verticale", "landscape":"inquadratura orizzontale",
    "negation":"prompt con negazione", "ambiguity":"formulazioni prudenti o ambigue",
    "language":"cambio di lingua", "paraphrase":"riformulazioni", "examples":"prompt con esempi",
    "order":"cambio dell’ordine", "format":"vincoli di formato", "length":"cambio di lunghezza",
    "canonical":"prompt canonico", "manual":"mutazioni manuali",
}


def _majority(values: list[str]) -> str | None:
    counts = Counter(value for value in values if value in VERDICTS)
    if not counts or counts["yes"] == counts["no"]: return None
    return counts.most_common(1)[0][0]


def _visual_tags(feature: dict[str, Any]) -> list[str]:
    tags=[]; brightness=feature.get("brightness"); contrast=feature.get("contrast"); edge=feature.get("edge_density")
    width,height=feature.get("width"),feature.get("height")
    if brightness is not None and brightness < .30: tags.append("dark")
    if brightness is not None and brightness > .76: tags.append("bright")
    if contrast is not None and contrast < .13: tags.append("low_contrast")
    if edge is not None and edge > .12: tags.append("fine_detail")
    if width and height and min(width,height) < 512: tags.append("small_image")
    if width and height and height > width*1.2: tags.append("portrait")
    if width and height and width > height*1.2: tags.append("landscape")
    return tags


def calculate_failure_diagnostics(rows: list[dict[str, Any]], features: dict[int, dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[int,int],list[dict[str,Any]]] = defaultdict(list)
    for row in rows:
        if row.get("ground_truth") in VERDICTS:
            grouped[(int(row["image_id"]),int(row["variant_id"]))].append(row)
    units=[]
    for (image_id,variant_id), unit_rows in grouped.items():
        answer=_majority([str(row.get("answer")) for row in unit_rows])
        if answer not in VERDICTS: continue
        first=unit_rows[0]; mutation=str(first.get("mutation_type") or "manual")
        visual=_visual_tags(features.get(image_id,{}))
        tags=[mutation,*visual]
        source=str(first.get("source_group") or "").strip()
        if source: tags.append(f"source:{source}")
        units.append({"image_id":image_id,"variant_id":variant_id,"variant_name":first.get("variant_name"),
            "mutation_type":mutation,"error":answer!=first["ground_truth"],"tags":tags,"visual_tags":visual,
            "format_failure":not all(bool(row.get("format_valid")) for row in unit_rows)})
    failures=sum(unit["error"] for unit in units); overall=failures/len(units) if units else 0.0
    candidates=sorted({tag for unit in units for tag in unit["tags"]})
    patterns=[]
    for tag in candidates:
        inside=[unit for unit in units if tag in unit["tags"]]; outside=[unit for unit in units if tag not in unit["tags"]]
        if len(inside)<2: continue
        inside_fail=sum(unit["error"] for unit in inside); rate=inside_fail/len(inside)
        comparison=sum(unit["error"] for unit in outside)/len(outside) if outside else overall
        low,high=wilson_interval(inside_fail,len(inside)); key=tag.split(":",1)[0]
        label=f"gruppo sorgente “{tag.split(':',1)[1]}”" if tag.startswith("source:") else LABELS.get(tag,tag)
        delta=rate-comparison
        patterns.append({"key":tag,"label":label,"samples":len(inside),"failures":inside_fail,
            "failure_rate":rate,"ci_low":low,"ci_high":high,"comparison_rate":comparison,"delta":delta,
            "example_image_ids":list(dict.fromkeys(unit["image_id"] for unit in inside if unit["error"]))[:6],
            "explanation":f"Il tasso d’errore con {label} è {rate*100:.1f}%, {abs(delta)*100:.1f} punti {'sopra' if delta>=0 else 'sotto'} gli altri casi."})
    patterns.sort(key=lambda item:(-item["delta"],-item["failures"],-item["samples"]))
    clusters: dict[tuple[str,str],list[dict[str,Any]]] = defaultdict(list)
    for unit in units:
        if not unit["error"]: continue
        visual=unit["visual_tags"][0] if unit["visual_tags"] else "other_visual"
        clusters[(unit["mutation_type"],visual)].append(unit)
    cluster_rows=[]
    for (mutation,visual),items in clusters.items():
        label=f"{LABELS.get(mutation,mutation)} · {LABELS.get(visual,'altre caratteristiche visive')}"
        cluster_rows.append({"label":label,"failures":len(items),"mutation_type":mutation,"visual_signal":visual,
            "image_ids":list(dict.fromkeys(item["image_id"] for item in items))[:8]})
    cluster_rows.sort(key=lambda item:(-item["failures"],item["label"]))
    feature_images={image_id for image_id,feature in features.items() if feature.get("brightness") is not None}
    invalid=sum(unit["format_failure"] for unit in units)
    return {"summary":{"evaluated_units":len(units),"failures":failures,"failure_rate":overall,
        "format_failures":invalid,"feature_coverage":len(feature_images)/len(features) if features else 0.0},
        "risk_patterns":[item for item in patterns if item["delta"]>0][:12],
        "all_patterns":patterns[:30],"clusters":cluster_rows[:12],
        "limitations":"Segnali visivi euristici su miniature locali: dettaglio fine non equivale necessariamente a testo piccolo e correlazione non implica causalità."}
