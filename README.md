# FragileVision

**A model leaderboard tells you who won. FragileVision tells you whether the result survives another wording.**

FragileVision is a local-first evidence laboratory for vision-language models. It evaluates the same semantic question, on the same images, through controlled prompt mutations and exposes exactly where the verdict changes.

No cloud account. No telemetry. No uploaded dataset. No opaque aggregate score.

[**Read the article**](https://VellBlue.github.io/fragilevision/) · [**Leggilo in italiano**](https://VellBlue.github.io/fragilevision/it/)

[Reference documentation](REFERENCE.md) · [Italian documentation](README.it.md)

## Why this is different

Most evaluation tools hold one prompt fixed and compare models. FragileVision
also holds the model fixed and tests the evaluation itself.

Three numbers carry that idea:

- **Prompt Fragility Score** — how often the verdict changes across controlled
  rewordings of the same question.
- **Repeat Drift** — how often it changes across identical repetitions, kept
  separate so that a stochastic backend cannot make a prompt look fragile.
- **Format integrity** — how much of the reported accuracy rests on a verdict
  read out of prose rather than out of schema-valid JSON.

The parser refuses to guess. An unterminated `<think>` block is recorded as
truncated rather than searched for the first verdict word inside the model's
own reasoning; prose naming more than one verdict decides nothing; and the
unaccented Italian `si` — a reflexive pronoun, not an affirmation — is never
read as a yes.

The remaining measurement surfaces — Evidence Gate, Failure Atlas, Model Arena,
dataset audit, train/test splitting, resumable runs, annotation reliability with
Krippendorff's α and Cohen's κ, the performance dashboard, run management, the
publishable Claim Card and the Replay Bundle — are documented in
**[REFERENCE.md](REFERENCE.md)**.

## The evidence workflow

```text
private image folder
        ↓ SHA-256 deduplication
blind multi-reviewer labels
        ↓ consensus · conflicts adjudicated
human ground truth ──→ semantic question
                           ↓
                 controlled mutations
          language · negation · examples
             order · format · length
                           ↓
             local model executions
                           ↓
      Failure Atlas + paired statistics
                           ↓
          Claim Card + Replay Bundle
```

Every run stores the exact final prompt, model adapter, seed, temperature, raw output, parser decision, latency and token counts. A dataset fingerprint binds image hashes, annotations, the panel that produced them and prompt variants into one reproducibility identifier.

## Quick start

Requirements: Python 3.11 or newer. There are no required runtime dependencies.

Outside macOS, install the optional extra: `pip install 'fragilevision[images]'`. The visual analysis — perceptual hash, near-duplicate detection, visual signals in the diagnosis — needs a local decoder, either macOS `sips` or Pillow. With neither, those checks cannot run: the audit says so as a high-severity warning instead of reporting an unexamined dataset as clean. Pillow also handles downscaling oversized inputs where `sips` is absent.

The two decoders do not agree pixel for pixel. Measured over 200 photographs, their hashes differ by at most 7 bits out of 64 (mean 2.1): identical-pair detection stays largely stable (98% agreement) but roughly a quarter of the "same scene" pairs move. The feature cache is keyed by engine so one database never mixes readings, and the audit names the engine behind its numbers — comparisons of near-duplicate counts across machines should compare like with like.

```bash
git clone https://github.com/VellBlue/fragilevision.git
cd fragilevision
python3 -m fragilevision
```

FragileVision opens at `http://127.0.0.1:7331`. The server refuses non-loopback bind addresses.

To use an isolated data directory:

```bash
python3 -m fragilevision --data-dir ./private-lab-data
```

Generate the rights-safe geometric demo dataset:

```bash
python3 scripts/create_demo.py
```

The 12 demo images deliberately repeat six visual configurations while remaining
byte-distinct. Importing `demo-images` as a plain folder is therefore expected to
raise a high-severity near-duplicate warning. This is a built-in demonstration of
the audit, not a claim that the sample is suitable evidence for a real evaluation.

### macOS app

```bash
bash scripts/build_macos_app.sh
```

Builds `dist/FragileVision.app` — a real, double-clickable, Dock-launchable
app — using only tools already on macOS (`sips`, `iconutil`, `codesign`). No
bundler and no `pip install`: the payload is a plain copy of the
`fragilevision` package, since it has zero required runtime dependencies.
Move the result to `/Applications`. To keep it in the Dock, drag
`FragileVision.app` there directly from Finder: the running server is a background
process, so it does not create a Dock or Cmd-Tab icon that can be pinned. Starting
the app opens no terminal; startup failures show a native alert, and everything
else — including the server's own request log — goes to
`~/Library/Logs/FragileVision/fragilevision.log`.

The zero-dependency launcher has no Cocoa **Quit** command. Closing the browser
does not stop the local server. To stop the app, run:

```bash
pkill -f "python.*-m fragilevision --port 7331"
```

This background-only behavior is the tradeoff that keeps the bundle a plain
Python package with no native wrapper.

On a fresh install with no provider configured yet, the app checks Ollama's
own default address and registers only the models Ollama itself reports as
vision-capable (read from its declared `capabilities` field, not guessed from
a model's name). Nothing else is auto-probed: unlike Ollama, an MLX runner,
LM Studio or llama.cpp share no single standard port, and guessing at one
risks silently registering an unrelated local service.

Then import the displayed `demo-images` path from the Dataset screen.

The Dataset screen has a native **Choose folder** button. FragileVision receives only the selected local path and copies supported images directly into managed storage; the browser does not upload the directory anywhere.

To explore the complete workflow without downloading a model, open **Models** and add the built-in Synthetic Prompt Stressor. It is deterministic, makes no network connection and intentionally produces prompt-dependent failures. Every resulting run and Claim Card is visibly marked `DEMO`.

## Supported model endpoints

- Ollama `/api/chat`
- OpenAI-compatible multimodal `/chat/completions`, including local MLX servers

Use **Detect models** to read the catalogue exposed by the selected private endpoint. **Choose model folder** is also available for MLX or other compatible servers that accept a local directory as their model identifier; Ollama users should normally use catalogue detection.

Provider endpoints are restricted to loopback, private IP ranges, link-local networks and Tailscale `*.ts.net` hosts. A public inference API is rejected by design.

## Security and privacy

- interface bound to `127.0.0.1` only, with `Host` header validation so a rebound DNS name cannot become same-origin;
- mutation requests protected by an unguessable per-process token;
- no CORS permission and restrictive browser security headers;
- no trackers, CDNs, fonts or third-party JavaScript;
- model endpoint allowlist restricted to private networks;
- native dataset/model directory pickers that do not transmit selected files;
- bounded JSON, image and model-response sizes;
- managed dataset never included in Replay Bundles.

FragileVision data is local, but it is not encrypted at rest by this first release. Use full-disk encryption for sensitive datasets. See [SECURITY.md](SECURITY.md).

## Tests

```bash
python3 -m unittest discover -s tests -v
node --check fragilevision/static/app.js
```

The tests cover parser honesty (truncated reasoning, ambiguous prose, Italian pronouns), endpoint isolation, `Host` validation against DNS rebinding, deterministic offline simulation, scene balancing, the Evidence Gate, paired statistics, matched Arena ranking, fragility/drift separation, consensus and adjudication rules, the agreement coefficients against their published reference datasets, per-model cost and memory aggregation, live ETA estimation, run rename/duplicate/archive/filter/export, the HTML and Markdown report builders (including Markdown table-cell escaping), capability-based vision-model auto-detection, mutation-token enforcement and static path traversal.

## Project status

Version `0.15.0` is an executable research preview. The storage format is versioned (schema 10) and migrates existing local databases in place, preserving run history, responses and existing ground truth, but may still change before `1.0`. Evaluation fingerprints recorded before `0.11.0` will not match after upgrading: annotation provenance now enters the hash.

Planned next surfaces:

- scene-cluster bootstrap confidence intervals (scene-balanced point estimates already ship);
- deterministic mutation recipes with semantic invariance checks;
- signed Replay Bundles;
- import/export compatibility with Hugging Face Community Evals;
- text, audio and video tasks using the same evidence contract.

## License

Apache-2.0. Dataset licenses remain separate from the code license.
