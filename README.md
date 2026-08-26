# FragileVision

**A model leaderboard tells you who won. FragileVision tells you whether the result survives another wording.**

FragileVision is a local-first evidence laboratory for vision-language models. It evaluates the same semantic question, on the same images, through controlled prompt mutations and exposes exactly where the verdict changes.

No cloud account. No telemetry. No uploaded dataset. No opaque aggregate score.

[Italian documentation](README.it.md)

## Why this is different

Most evaluation tools hold one prompt fixed and compare models. FragileVision also holds the model fixed and tests the evaluation itself.

It records seven distinct surfaces:

1. **Task performance** — accuracy, balanced accuracy and Wilson intervals.
2. **Naive baseline** — what a majority-class parrot gets without seeing an image.
3. **Prompt Fragility Score** — pairwise verdict disagreement across controlled prompt variants.
4. **Repeat Drift** — nondeterminism across identical repetitions, kept separate from prompt fragility.
5. **Format integrity** — whether the model actually returned the requested machine-readable answer, and how much of the reported accuracy rests on a verdict read out of prose rather than schema-valid JSON.
6. **Scene-balanced accuracy** — each independent source group gets equal influence, so a burst shoot cannot impersonate a large sample.
7. **Evidence Gate** — an explicit heuristic checklist that labels a claim insufficient, exploratory, reviewable or strong.
8. **Model Arena** — matched, sequential comparisons across local models with paired deltas and exact tests.
9. **Dataset audit** — near-duplicate pairs, integrity, class balance and a train/test split that cannot leak a scene across itself.
10. **Annotation reliability** — blind multi-reviewer ground truth with Krippendorff's α, Cohen's κ and an adjudication queue for the cases the panel could not settle.
11. **Performance dashboard** — latency, token throughput, error rate, a live ETA on running executions and a single honestly-labelled memory sample per model, cumulative across every project.
12. **Full run management** — rename, duplicate to rerun with the exact same configuration, archive without deleting, filter by project/status/provider/name and export the ledger as CSV.
13. **Publishable report** — the Claim Card gains inline SVG charts and a print stylesheet for a one-click PDF, plus a GitHub-flavored Markdown export with the same tables and a text-only bar chart, both included in the Replay Bundle.

This separation matters. A stochastic backend must not make a prompt look fragile, and a fluent paragraph containing the word “yes” must not silently pass as a valid JSON response.

The parser refuses to guess. An unterminated `<think>` block is recorded as truncated rather than searched for the first verdict word inside the model's own reasoning; prose naming more than one verdict decides nothing; and the unaccented Italian `si` — a reflexive pronoun, not an affirmation — is never read as a yes.

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

## Prompt Fragility Score

For each image and semantic question, FragileVision takes one representative verdict per prompt variant. It then compares every pair of valid variants:

```text
PFS = 100 × disagreeing variant pairs / comparable variant pairs
```

The score is a property of the observed model–dataset–prompt system, not a universal property of a model. The Claim Card says this explicitly and never converts the score into a global rank.

Repeated calls to the same variant are used for **Repeat Drift**, not PFS. Paired canonical-versus-alternative differences use the exact McNemar test. Accuracy intervals use Wilson's method.

## Evidence Gate

A dramatic score is not automatically a publishable claim. The Evidence Gate checks, in plain sight:

- number of annotated cases;
- number of independent source groups;
- controlled mutation depth;
- parsed-response coverage and exact-format rate;
- automatic, local-only cached downscaling for oversized inputs (original files are never modified);
- paired comparison count;
- majority-class imbalance.

Its thresholds are deliberately disclosed as heuristics. The grade describes evidence maturity, never universal model quality. The same checklist is embedded in the Claim Card and Replay Bundle.

## Failure Atlas

The Failure Atlas works backwards from the aggregate:

- which images changed verdict;
- which mutation caused the change;
- whether that change fixed or introduced an error;
- whether the same model repeats itself consistently;
- whether a claimed improvement exceeds the majority baseline;
- whether paired evidence is statistically distinguishable from noise.

It is designed for diagnosis, not decoration.

## Model Arena

Model Arena launches the same project, questions, variants, repetitions, temperature and seed across 2–8 configured models. Runs execute sequentially so local models do not compete for GPU or unified memory. Existing completed runs are grouped automatically by compatible protocol.

Every ranked number — accuracy, Wilson 95% interval, scene-balanced accuracy, prompt fragility, Repeat Drift, format integrity and latency — is computed on the units that *all* selected models answered readably. This is the point of the Arena and not a detail: ranking each model on whatever it happened to answer lets the model that returns nothing on the hard images finish first at 100% while the paired test correctly reports no difference. Each model's self-reported figure over its own units is still displayed next to its coverage, but never determines the ranking, and a comparison with no common units is refused rather than shown.

Every pair of models receives a paired accuracy delta, uncertainty interval, win/loss count and exact McNemar p-value. Repetitions are collapsed to one majority verdict per image/variant before accuracy is calculated, so repeated calls never pretend to be independent samples. Leader badges are withheld when the leaders are tied.

The result is explicitly scoped to the selected dataset and protocol; FragileVision never presents an Arena winner as a universal best model.

## Dataset audit

SHA-256 deduplication at import only catches byte-identical files. Two exports of
one photograph, or two frames of one setup, pass it untouched and then quietly
count as two independent pieces of evidence.

The audit derives a 64-bit difference hash from the thumbnail already extracted
for failure diagnosis and compares every pair. The 12-bit threshold was
calibrated against a real archive rather than chosen: two exports of one
photograph land at 0, two frames of one setup at 10, and genuinely different
photographs never fell below 17.

The finding that matters is not the duplicate count but **which pairs cross a
source group**: those defeat scene-balanced accuracy, which is the mechanism that
stops a burst shoot impersonating a large sample. They are raised as high
severity and shown side by side with their distance, because deciding whether two
photographs are one piece of evidence is a photographer's judgement, not a
threshold's.

The same pass reports missing, empty or altered files, dominant classes and
minorities too thin for the intervals to separate two models, resolution outliers
and how many images are downscaled before the model ever sees them. Checksum
verification is opt-in, since it re-reads the whole dataset, and the report always
states which of the two checks ran.

On real photography the hash survives aggressive re-exports — measured at no more
than 5 bits of drift down to 220 px and quality 20. On flat, uniform images such
as synthetic graphics or document scans, comparisons between neighbouring pixels
become noise and near-duplicates can escape. That limit is printed with the
results.

## Train and test

The split assigns whole scenes, never individual images. Units are source groups,
merged further wherever a near-duplicate pair crosses a group boundary, so two
frames of one scene cannot land on opposite sides and let the test set score on a
picture the training side already contains. It is deterministic from its seed,
enters the evaluation fingerprint and is named in `eval.yaml`.

A dataset that collapses into a single independence unit is refused rather than
split: a train-only assignment would look like a split and protect nothing. The
message says how many units the dataset actually has and how large the biggest
one is.

Runs and Arena batches can be confined to one side, and two runs on different
sides are never compared as if they were matched.

## Resumable runs

Every response is committed as it arrives, so a run is a sequence of checkpoints
rather than an all-or-nothing batch. A run can be paused and resumed, and an
application restart marks an interrupted run `paused` instead of `failed`.

On resume only the missing units execute. A response that reached the model is
never repeated — including an unreadable one, because an unparseable answer is a
result, not a gap — while calls that never landed are retried, which is what lets
a long evaluation survive the local model server going down halfway.

Progress counts stored verdicts rather than attempts, so a run against an
unreachable model reports what it actually obtained instead of a full bar, and a
run that finishes with units still missing says so and stays resumable. Pausing
one model in an Arena pauses the whole matched batch, since a partial comparison
is not the comparison that was asked for.

## Annotation reliability

Ground truth written by one person alone cannot be told apart from that person's
habits. The annotation console records every judgement against the reviewer who
made it, and the single ground-truth value used by every metric is *derived*
from those judgements rather than typed directly.

The consensus rule refuses to invent a winner. Unanimity and a strict majority
decide; an even split and a mere plurality do not. Both come back as a conflict
carrying `uncertain`, which the accuracy metrics already exclude — an unresolved
disagreement must be settled by a person before it can become a benchmark
number. Adjudication is that act, and it is stored with the adjudicator's name
beside it, alongside rather than instead of the labels it overrides. A case
nobody disputed cannot be overruled.

Blind mode is on by default: other reviewers' verdicts, the consensus, and even
how it was reached stay hidden until you have recorded your own.

| Coefficient | Reported when | Why |
|---|---|---|
| Krippendorff's α | always | the only one that tolerates partially overlapping subsets and a panel whose size changes per case |
| Fleiss' κ | constant panel size | classical, but genuinely undefined otherwise |
| Cohen's κ | per pair of reviewers | on the cases they both judged |
| Observed agreement | always | the honest fallback when κ is undefined |

Intervals come from a seeded bootstrap resampling **cases, not labels** — two
judgements on one photograph are not two independent observations of reviewer
behaviour. With a few dozen doubly annotated cases the interval is wide, and it
is shown that way.

Two limits are stated rather than hidden. The coefficients measure whether
reviewers resemble each other, not whether they are right: a panel sharing one
bias scores highly. And when everyone answers the same single category every
time, κ is undefined — chance already explains the agreement — so a dash is
printed instead of a fabricated 1.0.

A per-question α below 0.4 is raised as a warning about the *question*. If you
have to stop and think whether the tag applies, the tag is badly defined.

The Evidence Gate checks that at least 20% of annotated cases carry more than
one independent judgement — the standard reliability subsample — and grade A
now requires it.

## Performance dashboard

One row per configured model, aggregated across every project it has ever run
in — a model's real cost is a property of the model and the machine, not of
one project's dataset.

A failed call and a bad answer are counted separately: error rate is calls
that never reached the model; median and p95 latency, and token throughput,
are computed only over the calls that did, so one unreachable server does not
distort what a successful call actually costs. Throughput is reported twice on
purpose — median latency is the cost of a successful call, and a separate
"resa reale" (completed ÷ elapsed time) charges for the wall-clock time of
retries and failures too.

Every running execution shows a live ETA, drawn from that model's own last 200
settled calls rather than a fleet-wide average.

Memory gets one honestly-labelled sample per run, taken right after the first
settled response — once the model is definitely loaded — by reading Ollama's
`/api/ps` and matching the exact model tag, so a 4B and an 8B quantization are
never confused for the same footprint. It is a snapshot, not a continuous
measurement. OpenAI-compatible endpoints have no standard API for resident
memory, and the synthetic demo provider allocates nothing real: both show a
dash and a stated reason rather than an invented number.

## Run management

Every run can be renamed, duplicated to rerun with its exact configuration
(provider, questions, variants, repetitions, temperature, seed), archived, or
deleted.

Archiving sets a run aside without touching its data: every response and
metric stays intact and fully replayable, it just drops out of the live
ledger and the Arena/Failure Atlas pickers. An active run cannot be archived —
pause or stop it first — and a restored run must be explicitly unarchived
before it can be resumed, so nothing ever runs invisibly outside the ledger it
belongs in.

The ledger filters by project, status, provider and a name search, and
exports the currently filtered set as a CSV (name, project, provider, model,
status, timestamps, runtime, error) — useful for a quick spreadsheet look
across many runs without opening each one's Replay Bundle.

## Publishable report

The Claim Card (`report.html`) carries two inline SVG charts — a radial gauge
for the Prompt Fragility Score and a bar per variant with the majority
baseline marked — the same pictures the live Failure Atlas shows, so the
exported artifact and the app agree on how the result looks. No chart
library: plain SVG styled through the page's own CSS custom properties.

"Esporta PDF" calls the browser's own print dialog against a `@media print`
stylesheet that swaps in a light, ink-appropriate palette and hides the
button itself. No PDF library and no server-side rendering — the browser
already does this correctly once the CSS asks it to.

A Markdown export (`report.md`, also at `/api/runs/<id>/report.md`) carries
the same summary, Evidence Gate checklist, variant/comparison/parser tables
and ground-truth reliability section as GitHub-flavored Markdown, for a repo
README, a wiki page or a paste into an issue. Table cells are escaped against
a name that happens to contain `|` or a newline. In place of the SVG charts,
a block-character bar survives in a plain-text viewer with no image renderer
at all.

## Replay Bundle

Every completed run exports a ZIP containing:

- `manifest.json` — hashes, the consensus ground truth, every individual reviewer judgement, the inter-annotator agreement report, prompts, raw responses and metrics;
- `report.md` — the same Claim Card as GitHub-flavored Markdown;
- `eval.yaml` — portable evaluation configuration;
- `report.html` — self-contained Claim Card;
- `README.txt` — disclosure of what is and is not included.

Image pixels are excluded. This allows a private dataset to produce an auditable public artifact without leaking the dataset itself.

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
