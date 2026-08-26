"""Dataset integrity, balance and leak-free splitting.

Every check here answers one question: can the sample support the claim the
evaluation is about to make? None of it inspects a model.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import random
import statistics
from typing import Any

from .core import (MODEL_INPUT_MAX_EDGE, feature_engine, hamming_distance, image_dimensions,
                   sha256_file)


# Calibrated against a real archive: two exports of one photograph land at 0, two
# frames of one setup at 10, and genuinely different photographs never fell below
# 17. The gap between 11 and 15 is where the threshold belongs.
NEAR_DUPLICATE_THRESHOLD = 12
IDENTICAL_THRESHOLD = 4
MAX_PAIRWISE_IMAGES = 6000
RESOLUTION_OUTLIER_FACTOR = 2.0
VERDICTS = ("yes", "no", "uncertain", "exclude")


def scene_key(image: dict[str, Any]) -> str:
    """The unit of independence, matching how scene-balanced accuracy groups."""
    group = str(image.get("source_group") or "").strip()
    return group or f"image:{image['id']}"


def inspect_files(images: list[dict[str, Any]], *, verify_checksums: bool = False) -> dict[str, Any]:
    """Check that every managed file is still present, readable and unchanged.

    Checksums are optional because verifying them re-reads the whole dataset;
    without them a file edited in place since import stays undetected, which the
    report says plainly rather than implying a clean bill of health.
    """
    def examine(image: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        record = {"id": int(image["id"]), "filename": str(image.get("filename") or "")}
        path = Path(str(image["stored_path"]))
        try:
            if not path.is_file():
                return "missing", record | {"reason": "il file gestito non esiste più"}
            if path.stat().st_size <= 0:
                return "unreadable", record | {"reason": "file vuoto"}
            width, height = image_dimensions(path)
            if not width or not height:
                return "unreadable", record | {"reason": "intestazione immagine non leggibile"}
            if (width, height) != (image.get("width"), image.get("height")):
                return "changed", record | {"reason": f"dimensioni diverse da quelle registrate "
                                                      f"({width}×{height} invece di {image.get('width')}×{image.get('height')})"}
            if verify_checksums and sha256_file(path) != str(image["sha256"]):
                return "changed", record | {"reason": "il contenuto è cambiato dopo l’importazione"}
        except OSError as error:
            return "unreadable", record | {"reason": str(error)[:200]}
        return "ok", record

    results: dict[str, list[dict[str, Any]]] = {"missing": [], "unreadable": [], "changed": [], "ok": []}
    if images:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for bucket, record in pool.map(examine, images):
                results[bucket].append(record)
    return {
        "checked": len(images), "healthy": len(results["ok"]),
        "missing": results["missing"][:50], "unreadable": results["unreadable"][:50],
        "changed": results["changed"][:50],
        "missing_count": len(results["missing"]), "unreadable_count": len(results["unreadable"]),
        "changed_count": len(results["changed"]),
        "checksums_verified": verify_checksums,
    }


def analyze_resolution(images: list[dict[str, Any]]) -> dict[str, Any]:
    sized = [image for image in images if image.get("width") and image.get("height")]
    if not sized:
        return {"measured": 0, "outliers": [], "downscaled_for_model": 0, "orientation": {}}
    long_edges = [max(int(image["width"]), int(image["height"])) for image in sized]
    median_edge = statistics.median(long_edges)
    outliers = []
    for image in sized:
        edge = max(int(image["width"]), int(image["height"]))
        ratio = edge / median_edge if median_edge else 1.0
        if ratio >= RESOLUTION_OUTLIER_FACTOR or ratio <= 1 / RESOLUTION_OUTLIER_FACTOR:
            outliers.append({"id": int(image["id"]), "filename": str(image.get("filename") or ""),
                             "width": int(image["width"]), "height": int(image["height"]),
                             "ratio_to_median": ratio})
    orientation: Counter[str] = Counter()
    for image in sized:
        width, height = int(image["width"]), int(image["height"])
        orientation["verticale" if height > width * 1.05 else
                     "orizzontale" if width > height * 1.05 else "quadrata"] += 1
    return {
        "measured": len(sized),
        "median_long_edge": median_edge,
        "min_long_edge": min(long_edges), "max_long_edge": max(long_edges),
        "outliers": sorted(outliers, key=lambda item: -item["ratio_to_median"])[:24],
        "outlier_count": len(outliers),
        # Anything above the model input ceiling is resized before inference, so
        # the model never sees the resolution the dataset advertises.
        "downscaled_for_model": sum(1 for edge in long_edges if edge > MODEL_INPUT_MAX_EDGE),
        "model_input_max_edge": MODEL_INPUT_MAX_EDGE,
        "orientation": dict(orientation),
    }


def analyze_balance(questions: list[dict[str, Any]], annotations: list[dict[str, Any]],
                    image_count: int) -> list[dict[str, Any]]:
    by_question: dict[int, Counter[str]] = defaultdict(Counter)
    for row in annotations:
        by_question[int(row["question_id"])][str(row["value"])] += 1
    report = []
    for question in questions:
        counts = by_question[int(question["id"])]
        usable = counts["yes"] + counts["no"]
        annotated = sum(counts.values())
        majority = max(counts["yes"], counts["no"])
        minority = min(counts["yes"], counts["no"])
        warnings = []
        if not usable:
            warnings.append("nessun caso decidibile: senza sì/no non c’è niente da misurare")
        else:
            if majority / usable > .80:
                warnings.append(f"una classe copre il {100 * majority / usable:.0f}% dei casi decidibili: "
                                "un modello che risponde sempre uguale sembrerà bravo")
            if minority < 10:
                warnings.append(f"solo {minority} casi nella classe minoritaria: gli intervalli "
                                "di confidenza saranno troppo larghi per distinguere due modelli")
        if annotated < image_count:
            warnings.append(f"{image_count - annotated} immagini non ancora annotate per questa domanda")
        report.append({
            "question_id": int(question["id"]), "key": str(question.get("key") or ""),
            "label": str(question.get("label") or ""),
            "counts": {verdict: counts[verdict] for verdict in VERDICTS},
            "annotated": annotated, "unannotated": max(0, image_count - annotated),
            "decidable": usable,
            "majority_share": majority / usable if usable else 0.0,
            "warnings": warnings,
        })
    return report


def analyze_groups(images: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = Counter(scene_key(image) for image in images)
    named = {key: size for key, size in sizes.items() if not key.startswith("image:")}
    total = sum(sizes.values())
    largest = max(sizes.items(), key=lambda item: item[1]) if sizes else ("", 0)
    return {
        "count": len(sizes), "named_count": len(named),
        "ungrouped": sum(1 for key in sizes if key.startswith("image:")),
        "largest_name": largest[0], "largest_size": largest[1],
        "largest_share": largest[1] / total if total else 0.0,
        "sizes": sorted(({"name": key, "size": size} for key, size in named.items()),
                        key=lambda item: -item["size"])[:24],
    }


def find_near_duplicates(images: list[dict[str, Any]], hashes: dict[int, str],
                         threshold: int = NEAR_DUPLICATE_THRESHOLD) -> dict[str, Any]:
    """Compare every pair of perceptual hashes.

    SHA-256 deduplication at import only catches byte-identical files. Two
    exports of one photograph, or two frames of one setup, pass it untouched and
    then quietly count as two independent pieces of evidence.
    """
    entries = [(int(image["id"]), hashes.get(int(image["id"])) or "", image) for image in images]
    comparable = [entry for entry in entries if entry[1]]
    engine = feature_engine()
    if len(comparable) > MAX_PAIRWISE_IMAGES:
        return {"scanned": False, "threshold": threshold, "comparable": len(comparable),
                "unhashed": len(entries) - len(comparable), "engine": engine,
                "reason": f"Confronto a coppie non eseguito sopra {MAX_PAIRWISE_IMAGES} immagini",
                "pairs": [], "pair_count": 0, "cross_group_pairs": 0, "identical_pairs": 0}
    pairs = []
    for index, (first_id, first_hash, first_image) in enumerate(comparable):
        first_value = int(first_hash, 16)
        for second_id, second_hash, second_image in comparable[index + 1:]:
            distance = (first_value ^ int(second_hash, 16)).bit_count()
            if distance <= threshold:
                pairs.append({
                    "a_id": first_id, "b_id": second_id,
                    "a_filename": str(first_image.get("filename") or ""),
                    "b_filename": str(second_image.get("filename") or ""),
                    "a_group": scene_key(first_image), "b_group": scene_key(second_image),
                    "distance": distance,
                    "kind": "identica" if distance <= IDENTICAL_THRESHOLD else "stessa scena",
                    "same_group": scene_key(first_image) == scene_key(second_image),
                })
    pairs.sort(key=lambda item: (item["distance"], item["a_id"], item["b_id"]))
    cross = [pair for pair in pairs if not pair["same_group"]]
    return {
        "scanned": True, "threshold": threshold, "identical_threshold": IDENTICAL_THRESHOLD,
        "engine": engine,
        "comparable": len(comparable), "unhashed": len(entries) - len(comparable),
        "pairs": pairs[:120], "pair_count": len(pairs),
        "identical_pairs": sum(1 for pair in pairs if pair["distance"] <= IDENTICAL_THRESHOLD),
        # These are the ones that defeat scene balancing: near-identical frames
        # counted as two independent source groups.
        "cross_group_pairs": len(cross), "cross_group_examples": cross[:24],
    }


def _clusters(images: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> dict[int, list[int]]:
    """Merge source groups with near-duplicate links into independence units."""
    parent = {int(image["id"]): int(image["id"]) for image in images}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(first: int, second: int) -> None:
        left, right = find(first), find(second)
        if left != right:
            parent[max(left, right)] = min(left, right)

    by_group: dict[str, list[int]] = defaultdict(list)
    for image in images:
        by_group[scene_key(image)].append(int(image["id"]))
    for members in by_group.values():
        for other in members[1:]:
            union(members[0], other)
    for pair in pairs:
        if pair["a_id"] in parent and pair["b_id"] in parent:
            union(int(pair["a_id"]), int(pair["b_id"]))
    grouped: dict[int, list[int]] = defaultdict(list)
    for image in images:
        grouped[find(int(image["id"]))].append(int(image["id"]))
    return grouped


def build_split(images: list[dict[str, Any]], pairs: list[dict[str, Any]], *,
                seed: int = 0, test_ratio: float = 0.3) -> dict[str, Any]:
    """Assign whole independence units to train or test.

    Splitting image by image would put two frames of one scene on opposite sides
    and let the test set score on something it has already been tuned against.
    Units are source groups, merged further wherever a near-duplicate pair
    crosses a group boundary.
    """
    if not images:
        raise ValueError("Nessuna immagine da suddividere")
    test_ratio = max(0.05, min(0.9, float(test_ratio)))
    clusters = _clusters(images, pairs)
    ordered = sorted(clusters.values(), key=lambda members: (-len(members), members[0]))
    shuffled = list(ordered)
    random.Random(seed).shuffle(shuffled)
    target = round(len(images) * test_ratio)
    assignment: dict[int, str] = {}
    test_count = 0
    for members in shuffled:
        # A unit joins the test side only while doing so moves the split closer to
        # the requested ratio, so one large scene cannot swallow the whole test set.
        if abs((test_count + len(members)) - target) <= abs(test_count - target):
            for image_id in members:
                assignment[image_id] = "test"
            test_count += len(members)
        else:
            for image_id in members:
                assignment[image_id] = "train"
    if not test_count or test_count == len(images):
        # One independence unit cannot be split in two. Returning a train-only or
        # test-only assignment would look like a split and protect nothing.
        largest = max((len(members) for members in clusters.values()), default=0)
        raise ValueError(
            f"Impossibile suddividere senza contaminare: il dataset si riduce a {len(clusters)} "
            f"unità indipendenti e la più grande ne contiene {largest} su {len(images)}. "
            "Assegna gruppi sorgente più fini alle immagini, oppure accetta che questo "
            "dataset valga come un unico campione."
        )
    return {
        "assignment": assignment, "seed": seed, "requested_ratio": test_ratio,
        "train": len(images) - test_count, "test": test_count,
        "achieved_ratio": test_count / len(images),
        "clusters": len(clusters),
        "largest_cluster": max((len(members) for members in clusters.values()), default=0),
    }


def inspect_split(images: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Report the split actually stored, and any leak across it."""
    assigned = {int(image["id"]): str(image.get("split") or "") for image in images}
    counts = Counter(value or "non assegnate" for value in assigned.values())
    leaks = [pair for pair in pairs
             if assigned.get(pair["a_id"]) and assigned.get(pair["b_id"])
             and assigned[pair["a_id"]] != assigned[pair["b_id"]]]
    return {
        "train": counts.get("train", 0), "test": counts.get("test", 0),
        "unassigned": counts.get("non assegnate", 0),
        "assigned": counts.get("train", 0) + counts.get("test", 0),
        # A leak means a near-duplicate sits on both sides: the test set is
        # scoring on a picture the train side already contains.
        "leak_pairs": leaks[:24], "leak_count": len(leaks),
    }


def audit_dataset(images: list[dict[str, Any]], questions: list[dict[str, Any]],
                  annotations: list[dict[str, Any]], hashes: dict[int, str], *,
                  verify_checksums: bool = False) -> dict[str, Any]:
    integrity = inspect_files(images, verify_checksums=verify_checksums)
    resolution = analyze_resolution(images)
    balance = analyze_balance(questions, annotations, len(images))
    groups = analyze_groups(images)
    duplicates = find_near_duplicates(images, hashes)
    split = inspect_split(images, duplicates["pairs"])

    warnings: list[dict[str, str]] = []
    if integrity["missing_count"] or integrity["unreadable_count"]:
        warnings.append({"severity": "alta", "area": "integrità",
                         "text": f"{integrity['missing_count'] + integrity['unreadable_count']} immagini "
                                 "non sono più leggibili: verranno registrate come errori a ogni esecuzione."})
    if integrity["changed_count"]:
        warnings.append({"severity": "alta", "area": "integrità",
                         "text": f"{integrity['changed_count']} immagini sono cambiate dopo l’importazione: "
                                 "il fingerprint del dataset non descrive più i file su disco."})
    unhashed = int(duplicates.get("unhashed") or 0)
    if unhashed:
        # Without this the page shows zero near-duplicate pairs and no warning at
        # all, which reads as a clean sample. It is the one failure mode where
        # silence is a wrong answer rather than a missing one.
        engine = duplicates.get("engine")
        cause = (f"il decodificatore locale ({engine}) non è riuscito ad aprirle" if engine else
                 "su questo sistema non c’è un decodificatore locale: serve macOS (sips) "
                 "oppure Pillow installato (pip install pillow)")
        if unhashed >= len(images):
            warnings.append({"severity": "alta", "area": "analisi visiva",
                             "text": f"nessuna delle {len(images)} immagini ha un’impronta percettiva: "
                                     "il controllo dei quasi duplicati non ha confrontato niente e "
                                     "zero coppie qui non vuol dire dataset pulito — "
                                     f"{cause}."})
        else:
            warnings.append({"severity": "alta", "area": "analisi visiva",
                             "text": f"{unhashed} immagini su {len(images)} sono senza impronta "
                                     "percettiva: restano fuori dal controllo dei quasi duplicati e "
                                     f"dai segnali visivi della diagnosi — {cause}."})
    if duplicates["scanned"] and duplicates["cross_group_pairs"]:
        warnings.append({"severity": "alta", "area": "duplicati",
                         "text": f"{duplicates['cross_group_pairs']} coppie quasi identiche stanno in gruppi "
                                 "sorgente diversi: la scene-balanced accuracy le conta come prove "
                                 "indipendenti mentre non lo sono."})
    elif duplicates["scanned"] and duplicates["pair_count"]:
        warnings.append({"severity": "media", "area": "duplicati",
                         "text": f"{duplicates['pair_count']} coppie quasi identiche, tutte già nello stesso "
                                 "gruppo sorgente: il bilanciamento per scena le gestisce."})
    if split["leak_count"]:
        warnings.append({"severity": "alta", "area": "split",
                         "text": f"{split['leak_count']} coppie quasi identiche sono divise fra train e test: "
                                 "il test sta misurando su immagini che il train già contiene."})
    if groups["count"] and groups["largest_share"] > .5:
        warnings.append({"severity": "media", "area": "gruppi",
                         "text": f"il gruppo “{groups['largest_name']}” copre il "
                                 f"{100 * groups['largest_share']:.0f}% del dataset: il risultato parlerà "
                                 "soprattutto di quella scena."})
    if groups["ungrouped"] > 1:
        warnings.append({"severity": "bassa", "area": "gruppi",
                         "text": f"{groups['ungrouped']} immagini senza gruppo sorgente: ognuna conta come "
                                 "scena a sé, il che è corretto solo se lo sono davvero."})
    for item in balance:
        for text in item["warnings"]:
            warnings.append({"severity": "media", "area": f"domanda “{item['label']}”", "text": text})
    if resolution.get("downscaled_for_model"):
        warnings.append({"severity": "bassa", "area": "risoluzione",
                         "text": f"{resolution['downscaled_for_model']} immagini superano "
                                 f"{resolution['model_input_max_edge']} px e vengono ridotte prima "
                                 "dell’inferenza: il modello non vede la risoluzione dichiarata."})
    order = {"alta": 0, "media": 1, "bassa": 2}
    warnings.sort(key=lambda item: order.get(item["severity"], 3))
    return {
        "images": len(images), "questions": len(questions),
        "integrity": integrity, "resolution": resolution, "balance": balance,
        "groups": groups, "near_duplicates": duplicates, "split": split,
        "warnings": warnings,
        "limitations": "L’impronta percettiva confronta la composizione su una miniatura locale: "
                       "riconosce lo stesso scatto e la stessa scena, non il fatto che due fotografie "
                       "diverse mostrino lo stesso soggetto. Su fotografia reale regge a riesportazioni "
                       "aggressive (misurato: al più 5 bit di scarto fino a 220 px e qualità 20), ma su "
                       "immagini a campi piatti e uniformi — grafica sintetica, scansioni di documenti — "
                       "i confronti fra pixel vicini diventano rumore e i quasi duplicati possono sfuggire.",
    }
