"""Operational signals per configured model: cost, reliability and memory.

Unlike metrics.py, none of this needs ground truth or even a finished run — it
answers a narrower question than "is the model right": what does calling it
actually cost, across every project that has ever used it. The same honesty
rule applies anyway: a number nobody could observe is a dash, not a guess.
"""

from __future__ import annotations

from collections import defaultdict
import statistics
from typing import Any

from .metrics import percentile


MIN_SETTLED_FOR_STATS = 5
HIGH_ERROR_RATE = 0.10
ACTIVE_STATUSES = {"queued", "running"}
_UNITS = ("B", "KB", "MB", "GB", "TB")


def format_bytes(value: int | None) -> str | None:
    if value is None:
        return None
    size = float(value)
    for unit in _UNITS[:-1]:
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {_UNITS[-1]}"


def median_latency_seconds(latencies_ms: list[float]) -> float | None:
    """Historical per-call cost. Shared by the dashboard and by run ETAs, so
    the two numbers a user compares — "usually takes" and "should finish in"
    — are always the same statistic, never two different approximations."""
    return (statistics.median(latencies_ms) / 1000) if latencies_ms else None


def _provider_stats(responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Split calls that reached the model from calls that never did.

    A response is "settled" once the model answered, even unreadably — an
    unparseable verdict is a result. A row with `parser == 'error'` is a call
    that never landed at all, which is what error rate is meant to count.
    """
    settled = [row for row in responses if str(row.get("parser")) != "error"]
    errored = [row for row in responses if str(row.get("parser")) == "error"]
    latencies = [float(row["latency_ms"]) for row in settled if row.get("latency_ms") is not None]
    prompt_tokens = [int(row["prompt_tokens"]) for row in settled if row.get("prompt_tokens") is not None]
    completion_tokens = [int(row["completion_tokens"]) for row in settled if row.get("completion_tokens") is not None]
    paired = [(int(row["completion_tokens"]), float(row["latency_ms"])) for row in settled
              if row.get("completion_tokens") is not None and row.get("latency_ms")]
    paired_seconds = sum(ms for _, ms in paired) / 1000
    format_checked = [row for row in settled if row.get("format_valid") is not None]
    return {
        "responses_total": len(responses), "responses_settled": len(settled),
        "responses_errored": len(errored),
        "error_rate": len(errored) / len(responses) if responses else 0.0,
        "format_rate": (sum(bool(row.get("format_valid")) for row in format_checked) / len(format_checked)
                        if format_checked else None),
        "median_ms": statistics.median(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 0.95) if latencies else None,
        "min_ms": min(latencies) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
        "avg_prompt_tokens": statistics.mean(prompt_tokens) if prompt_tokens else None,
        "avg_completion_tokens": statistics.mean(completion_tokens) if completion_tokens else None,
        "tokens_per_second": (sum(tokens for tokens, _ in paired) / paired_seconds) if paired_seconds else None,
        "estimated_seconds_per_response": median_latency_seconds(latencies),
    }


def _memory_warning(entry: dict[str, Any]) -> dict[str, str] | None:
    if entry["is_demo"] or entry["memory_bytes"] is None:
        return None
    vram = entry["memory_vram_bytes"]
    if vram is not None and entry["memory_bytes"] and abs(entry["memory_bytes"] - vram) / entry["memory_bytes"] < 0.05:
        return {"severity": "bassa",
                "text": "Memoria e VRAM quasi identiche: probabile memoria unificata (Apple Silicon), non una "
                        "GPU dedicata separata dalla RAM di sistema."}
    return None


def _warnings(entry: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if entry["is_demo"]:
        warnings.append({"severity": "media",
                         "text": "Simulatore sintetico: non consuma memoria reale e il tempo per risposta "
                                 "non rappresenta un modello vero."})
    elif entry["responses_settled"] < MIN_SETTLED_FOR_STATS:
        warnings.append({"severity": "bassa",
                         "text": f"Solo {entry['responses_settled']} risposte riuscite: le statistiche non "
                                 "sono ancora stabili."})
    if entry["responses_total"] >= MIN_SETTLED_FOR_STATS and entry["error_rate"] > HIGH_ERROR_RATE:
        warnings.append({"severity": "alta",
                         "text": f"{100 * entry['error_rate']:.0f}% delle chiamate non ha raggiunto il "
                                 "modello: controlla che il server locale sia raggiungibile."})
    if not entry["is_demo"]:
        if entry["memory_observable"] and entry["memory_bytes"] is None and entry["runs_total"]:
            warnings.append({"severity": "bassa",
                             "text": "Memoria non ancora campionata per questo modello: riprova dopo "
                                     "un’esecuzione, oppure usa “Verifica memoria adesso”."})
        elif not entry["memory_observable"]:
            warnings.append({"severity": "bassa",
                             "text": "Endpoint OpenAI-compatibile: FragileVision non ha un’API standard per "
                                     "leggere la memoria occupata, quindi non mostra un numero al suo posto."})
    memory_warning = _memory_warning(entry)
    if memory_warning:
        warnings.append(memory_warning)
    order = {"alta": 0, "media": 1, "bassa": 2}
    warnings.sort(key=lambda item: order.get(item["severity"], 3))
    return warnings


def calculate_performance(providers: list[dict[str, Any]], responses: list[dict[str, Any]],
                          runs: list[dict[str, Any]]) -> dict[str, Any]:
    """One row per configured model, aggregated across every project it has run in.

    A model's real cost and reliability are properties of the model and the
    machine, not of one project's dataset, so more history — from anywhere —
    makes every median in this report more trustworthy, not less relevant.
    """
    by_provider_responses: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in responses:
        by_provider_responses[int(row["provider_id"])].append(row)
    by_provider_runs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in runs:
        by_provider_runs[int(row["provider_id"])].append(row)

    models = []
    for provider in providers:
        provider_id = int(provider["id"])
        own_responses = by_provider_responses.get(provider_id, [])
        own_runs = by_provider_runs.get(provider_id, [])
        stats = _provider_stats(own_responses)

        elapsed_runs = [row for row in own_runs if float(row.get("runtime_seconds") or 0) > 0]
        total_elapsed = sum(float(row["runtime_seconds"]) for row in elapsed_runs)
        total_completed = sum(int(row.get("completed") or 0) for row in elapsed_runs)
        # Throughput measured this way, unlike the median above, is charged for
        # the wall-clock time of failed calls and retries: it is what a run
        # actually cost, not what a successful call happens to cost.
        memory_reading = max((row for row in own_runs if row.get("memory_bytes") is not None),
                             key=lambda row: float(row.get("memory_sampled_at") or 0), default=None)
        last_used_at = max([float(row.get("created_at") or 0) for row in own_responses]
                           + [float(row.get("created_at") or 0) for row in own_runs], default=0.0)

        entry = {
            "provider_id": provider_id, "provider_name": provider.get("name"),
            "model": provider.get("model"), "kind": provider.get("kind"),
            "is_demo": bool(provider.get("is_demo")),
            **stats,
            "responses_per_second": (total_completed / total_elapsed) if total_elapsed else None,
            "runs_total": len(own_runs),
            "runs_active": sum(1 for row in own_runs if row.get("status") in ACTIVE_STATUSES),
            "runs_failed": sum(1 for row in own_runs if row.get("status") == "failed"),
            "last_used_at": last_used_at or None,
            "memory_bytes": memory_reading["memory_bytes"] if memory_reading else None,
            "memory_vram_bytes": memory_reading["memory_vram_bytes"] if memory_reading else None,
            "memory_sampled_at": memory_reading["memory_sampled_at"] if memory_reading else None,
            "memory_observable": provider.get("kind") == "ollama" and not provider.get("is_demo"),
        }
        entry["memory_display"] = format_bytes(entry["memory_bytes"])
        entry["memory_vram_display"] = format_bytes(entry["memory_vram_bytes"])
        entry["warnings"] = _warnings(entry)
        models.append(entry)

    models.sort(key=lambda item: (-item["responses_total"], (item["provider_name"] or "").casefold()))
    return {
        "models": models,
        "method": ("Latenza, token e tasso d’errore sono cumulativi su tutte le esecuzioni passate del "
                   "modello, in ogni progetto: più storia c’è, più le mediane sono stabili. La memoria è "
                   "un singolo campione preso da Ollama subito dopo la prima risposta di ogni esecuzione, "
                   "non una misura continua: cambia con la quantizzazione, il contesto e cos’altro gira "
                   "sulla macchina in quel momento."),
        "limitations": ("Il tempo per risposta include la rete e la coda del server locale, non soltanto "
                        "l’inferenza. Per gli endpoint OpenAI-compatibili la memoria non è osservabile: non "
                        "esiste un’API standard per leggerla, e FragileVision non mostra un numero al suo "
                        "posto."),
    }


def estimate_eta_seconds(latencies_ms: list[float], pending: int) -> float | None:
    """Minutes left for an active run, from this model's own recent calls."""
    if pending <= 0:
        return 0.0
    per_response = median_latency_seconds(latencies_ms)
    return per_response * pending if per_response is not None else None
