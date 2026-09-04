# FragileVision reference

Detailed documentation for each measurement surface. Start from the
[README](README.md) for the overview, installation and quick start.

## Contents

- [Prompt Fragility Score](#prompt-fragility-score)
- [When “person” includes a painting](#when-person-includes-a-painting)
- [Evidence Gate](#evidence-gate)
- [Failure Atlas](#failure-atlas)
- [Model Arena](#model-arena)
- [Dataset audit](#dataset-audit)
- [Train and test](#train-and-test)
- [Resumable runs](#resumable-runs)
- [Annotation reliability](#annotation-reliability)
- [Performance dashboard](#performance-dashboard)
- [Run management](#run-management)
- [Publishable report](#publishable-report)
- [Replay Bundle](#replay-bundle)

## Prompt Fragility Score

For each image and semantic question, FragileVision takes one representative verdict per prompt variant. It then compares every pair of valid variants:

```text
PFS = 100 × disagreeing variant pairs / comparable variant pairs
```

The score is a property of the observed model–dataset–prompt system, not a universal property of a model. The Claim Card says this explicitly and never converts the score into a global rank.

Repeated calls to the same variant are used for **Repeat Drift**, not PFS. Paired canonical-versus-alternative differences use the exact McNemar test. Accuracy intervals use Wilson's method.

## When “person” includes a painting

One exploratory run exposed a category that existed in the labels but not in the question. Qwen3-VL 4B was asked `Nella fotografia c'è più di una persona?` across 98 photographs. The ground truth counted only people physically present when the photograph was made; painted, photographed and sculpted figures counted as no.

There were eight readable false positives. All eight images contained people represented in paintings, frescoes or sculpture, but no physically present person. The model returned valid JSON without an explanation, so this does not establish why it answered yes. Its reading is also defensible: the prompt never said whether “person” meant someone in the physical scene or anyone represented inside the image.

This is an exploratory observation from one model, one source group and one wording, with nine additional unreadable responses—not a general claim about Qwen3-VL. The next comparison should make the intended category explicit by excluding paintings, photographs, statues and screens. The longer account is in the [project article](https://VellBlue.github.io/fragilevision/#article).

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

