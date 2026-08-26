# Changelog

## Unreleased

Visual analysis outside macOS, and an audit that admits when it has none.

- **The silent false negative is gone.** `_thumbnail_pixels()` was bound to
  macOS `sips`, so on Linux and Windows the perceptual hash and every visual
  signal came back empty — and an empty comparison produced zero near-duplicate
  pairs, no warning, and a dataset that looked clean. On the demo dataset the
  same button reported 34 near-identical pairs at high severity on macOS and
  nothing at all elsewhere.
- **Pillow fallback.** `feature_engine()` now picks `sips` on macOS and Pillow
  everywhere it is installed, mirroring what `prepare_model_image()` already
  did. Off macOS, `pip install 'fragilevision[images]'` restores the visual
  analysis: the demo dataset goes from 0 pairs to 31, against 34 with `sips`.
- **The engine is part of the cache key.** The two decoders do not produce the
  same thumbnail: measured on 200 photographs, their hashes differ by at most
  7 bits out of 64 (mean 2.1). Identical-pair detection barely moves (98%
  agreement) but about a quarter of the "same scene" pairs do, so
  `image_features` is now keyed by `sips-bmp-v2` or `pillow-bilinear-v1` and a
  single database can never compare readings from two engines. Existing macOS
  caches keep their key and stay valid. `NEAR_DUPLICATE_THRESHOLD` was left
  alone: it is calibrated against a real archive, not against a fallback.
- **When no decoder exists at all**, the audit raises a high-severity warning
  naming the cause and the fix, and the near-duplicate tile shows "—" with
  "analisi visiva non disponibile" instead of a reassuring zero. The tile also
  names the engine behind its numbers, and counts any image the decoder failed
  to open.
- **The demo now explains its own warning.** Its 12 geometric images deliberately
  contain six repeated visual configurations, so both the generator and the quick
  start say that a high-severity near-duplicate finding is expected and useful.
- **The macOS bundle documentation matches its background-only launcher.** The app
  must be dragged from Finder to the Dock because it creates no running Dock icon;
  closing the browser does not stop it, and the documented `pkill` command does.

## 0.15.0 — 2026-08-26

Standalone macOS app. Every previous release still needed a terminal open to
start it.

- **`scripts/build_macos_app.sh`** builds `FragileVision.app` — a real,
  double-clickable, Dock-launchable bundle — using only tools already on
  macOS (`sips`, `iconutil`, `codesign`). No bundler, no `pip install` step:
  the app has zero required runtime dependencies, so a plain copy of the
  `fragilevision` package is the entire payload.
- **No terminal, ever.** The launcher (`Contents/MacOS/FragileVision`) finds
  a working Python 3.11+ at the common install locations — a double-click
  from Finder does not inherit the interactive shell's `PATH` — starts the
  server, and lets it open the browser exactly as `python3 -m fragilevision`
  already does from a shell. A second launch while it is already running
  reopens the browser instead of failing to bind the same port a second time.
- **Failures a user can actually see.** With no terminal to print to, a
  fatal startup problem (no compatible Python found) shows a native alert
  instead of vanishing; everything else — including the server's own request
  log — goes to `~/Library/Logs/FragileVision/fragilevision.log`, started
  with Python's unbuffered mode so a force-quit doesn't lose it.
- **Ad-hoc code signing**, applied by the build script. Apple Silicon refuses
  to run a completely unsigned executable at all, so this is required, not
  cosmetic; no Developer ID or notarization needed since a locally built app
  that is never downloaded never carries the quarantine flag Gatekeeper's
  "unidentified developer" dialog reacts to.
- **Automatic local model configuration.** On a fresh install with no
  provider configured yet, the app checks Ollama's own default address
  (`127.0.0.1:11434`) — the one genuinely universal convention — and
  registers only the models Ollama itself reports as vision-capable, read
  from its own declared `capabilities` field rather than guessed from a
  model's name. An embedding model like `bge-m3` is excluded because Ollama
  says so, not because of a name pattern. Runs once, only while no provider
  exists yet, off the startup path in a background thread so the server
  still binds its port immediately even if the probe is slow.
- Other local-only OpenAI-compatible servers (MLX runners, LM Studio,
  llama.cpp) are never auto-probed: unlike Ollama's port, none of them share
  a single standard address, and guessing at one risks silently registering
  an unrelated local service.

## 0.14.0 — 2026-08-26

Publishable report. The Claim Card had every number; nothing to look at, and
no way out of HTML.

- **Charts in the Claim Card.** An inline SVG radial gauge for the Prompt
  Fragility Score and a horizontal bar per variant with the majority baseline
  marked — the same two pictures the live Failure Atlas already shows,
  reproduced in the exported artifact instead of left behind in the app. No
  chart library: plain SVG, styled through the same CSS custom properties as
  the rest of the page.
- **A print stylesheet, and a button that uses it.** `@media print` swaps the
  palette for a light, ink-appropriate one and hides the export button itself;
  "Esporta PDF" is `window.print()` against that stylesheet. No PDF library,
  no server-side rendering — the browser already does this correctly once the
  CSS asks it to.
- **Markdown export**, for a repo README, a wiki page or a paste into an
  issue: the same summary, Evidence Gate checklist, variant/comparison/parser
  tables and ground-truth reliability section as the HTML report, as
  GitHub-flavored Markdown. Table cells are escaped against a run or variant
  name that happens to contain `|` or a newline. In place of the SVG charts, a
  block-character bar (`████████░░`) survives in a plain-text viewer with no
  image renderer at all.
- Reachable at `/api/runs/<id>/report.md`, and included in the Replay Bundle
  as `report.md` alongside the existing `report.html`, `eval.yaml` and
  `manifest.json`.

## 0.13.0 — 2026-08-25

Full run management. Deletion already existed; nothing else did.

- **Rename.** A run's claim name can be edited after the fact, from a small
  dialog, without touching its recorded configuration or results.
- **Duplicate.** Rerun a past experiment with its exact configuration —
  provider, questions, variants, repetitions, temperature, seed — in one
  action. The copy starts immediately rather than sitting idle as a fake
  `queued` row, which `resume_run()` would otherwise refuse to touch.
- **Archive.** A completed, failed or cancelled run can be set aside without
  deleting it: every response and metric stays intact and fully replayable,
  it just stops cluttering the live ledger and the Arena/Failure Atlas
  pickers. An active run cannot be archived — pause or stop it first — and a
  restored run must be unarchived before it can be resumed, so nothing ever
  runs invisibly outside the ledger it belongs in.
- **Filter.** The ledger filters by project, status, provider and a name
  search, entirely client-side over the already-polled list — no extra
  request for the common case. A dedicated "mostra archiviate" toggle fetches
  the archive on demand, since those rows are deliberately excluded from the
  live, polled state.
- **Export.** A CSV of the currently filtered runs — name, project, provider,
  model, status, timestamps, runtime, error — written with a UTF-8 BOM so
  Excel opens accented Italian labels correctly. The export uses the same
  filters as the ledger, independent of its live 50-row cap.
- Database schema 10: `archived_at` on `runs`. Existing databases migrate in
  place.

## 0.12.0 — 2026-08-25

Performance dashboard. Latency and tokens were already stored on every
response; nothing had ever been asked to add them up.

- **One row per configured model, cumulative across every project it has run
  in.** A model's real cost is a property of the model and the machine, not of
  one project's dataset: more history from anywhere makes the medians more
  stable, not less relevant.
- **A failed call and a bad answer are counted separately.** Error rate is
  calls that never reached the model; format rate and accuracy are about calls
  that did. Median and p95 latency, and token throughput, are computed only
  over settled calls, so one unreachable server does not distort what a
  successful call actually costs.
- **Two different throughput numbers, on purpose.** Median latency reflects
  the cost of a successful call; a separate "resa reale" (`completed ÷
  runtime_seconds`, per the run's own accounting) charges for the wall-clock
  time of retries and failures too. Neither one is asked to stand in for the
  other.
- **Live ETA on every running execution**, from that model's own last 200
  settled calls — not a fleet-wide average, and bounded so a busy provider
  does not turn a one-second poll into a full table scan.
- **A single, honestly-labelled memory sample per run.** Right after a run's
  first settled response — once the model is definitely loaded, not before —
  FragileVision reads Ollama's `/api/ps` and matches the exact model tag; a
  4B and an 8B quantization are never confused for the same footprint. It is a
  snapshot, not a continuous measurement, and the dashboard says so. A
  "Verifica memoria adesso" button repeats the read on demand for a model
  nothing has run yet this session.
- **Memory is never fabricated for what cannot report it.** OpenAI-compatible
  endpoints have no standard API for resident memory, and the synthetic demo
  provider allocates nothing real; both show a dash and an explicit reason
  instead of a number. When the reading comes back and VRAM sits within 5% of
  total memory, a note flags probable unified memory (Apple Silicon) rather
  than a dedicated GPU.
- Database schema 9: `memory_bytes`, `memory_vram_bytes` and
  `memory_sampled_at` on `runs`. Existing databases migrate in place.

## 0.11.0 — 2026-08-25

Scientific annotation. The ground truth was the one number in the tool nobody
was allowed to question.

- **One reviewer per judgement, not one judgement per case.** Every verdict is
  now stored against the person who wrote it, in a new `annotation_labels`
  table. The old single ground-truth row survives as a *derived* consensus: it
  is recomputed from the labels on every change, so a reviewer changing their
  mind can never leave a stale verdict behind in an evaluation.
- **The consensus rule refuses to invent a winner.** Unanimity and a strict
  majority decide. An even split and a mere plurality do not: both are recorded
  as `conflict` carrying `uncertain`, the value the accuracy metrics already
  exclude. An unresolved disagreement that quietly became a benchmark number
  would be exactly the false confidence this tool exists to expose.
- **Adjudication is a named human act.** A contested case can be settled by a
  person, and the ruling is stored with their name next to it, alongside — never
  instead of — the independent labels it overrides. A case nobody disputed
  cannot be overruled at all.
- **Krippendorff's α as the headline coefficient**, because it is the only one
  of the three that survives the shape of real annotation work: reviewers who
  label different, partially overlapping subsets and a panel whose size changes
  from case to case. Fleiss' κ is shown only where it is actually defined — a
  constant panel — and Cohen's κ only pairwise. Verified against the published
  reference datasets: α = 0.743 and κ = 0.2099 reproduce exactly.
- **95% intervals from a seeded bootstrap over cases, not over labels.** Two
  judgements on one photograph are not two independent observations of reviewer
  behaviour. With a couple of dozen doubly annotated cases the interval comes
  back embarrassingly wide, which is the honest answer and is shown as such.
- **Perfect agreement on one category reports no coefficient at all.** When two
  reviewers answer the same single category every time, chance already explains
  everything and κ is undefined; the observed agreement is printed next to the
  dash rather than a fabricated 1.0.
- **Blind mode by default.** Other reviewers' verdicts, the consensus, and even
  how it was reached stay hidden until you have recorded your own. Anchoring on
  a colleague's call is the cheapest way to manufacture an agreement figure.
- **Disagreement points at the question, not at the reviewer.** A per-question
  α below 0.4 is raised as a warning about the *tag*: if you have to stop and
  think whether it applies, it is badly defined.
- **The Evidence Gate now asks who checked the labels.** A new check passes when
  at least 20% of annotated cases carry more than one independent judgement —
  the standard reliability subsample — and grade A now requires it.
- **The claim carries its panel.** The run summary reports how many cases were
  verified, adjudicated and dropped as unresolved; the Claim Card gains a ground
  truth reliability section; and the Replay Bundle (`replay@2`) ships every
  individual judgement and the full agreement report, still without pixels.
- **The evaluation fingerprint now covers annotation provenance.** A verdict one
  reviewer wrote alone and the same verdict three reviewers reached
  independently are not the same ground truth, so they no longer hash alike.
  Fingerprints recorded before this release will not match after upgrading.
- Database schema 8: `annotation_labels`, and `agreement`, `label_count`,
  `distinct_values` and `adjudicated_by` on `annotations`. Existing ground truth
  migrates in place, becoming its original author's own first label rather than
  being discarded.

## 0.10.0 — 2026-08-24

Dataset audit and leak-free splitting. SHA-256 deduplication only ever caught
byte-identical files; everything below is about what it let through.

- **Near-duplicate detection.** A 64-bit difference hash is derived from the
  thumbnail already extracted for failure diagnosis, so no new dependency and no
  second pass over the images. The threshold of 12 bits was calibrated against a
  real archive rather than chosen: two exports of one photograph landed at 0, two
  frames of one setup at 10, and genuinely different photographs never below 17.
- **The finding that matters is cross-group.** Near-identical frames filed under
  two different source groups are counted as two independent pieces of evidence
  by scene-balanced accuracy. Those pairs are raised as high severity and shown
  side by side with their distance, so the judgement stays with the photographer.
- **Train/test split by scene, never by image.** Units are source groups, merged
  further wherever a near-duplicate pair crosses a group boundary, so two frames
  of one scene can never land on opposite sides. The split is deterministic from
  its seed, enters the evaluation fingerprint and is named in `eval.yaml`.
- **A dataset that is one scene is refused, not faked.** Returning a train-only
  assignment would look like a split and protect nothing, so it says how many
  independent units the dataset actually collapses to.
- **Runs can be confined to one side.** A split nothing executes against is
  decoration, so the runner, the Arena and the reproducibility manifest all carry
  it, and two runs on different sides are never compared as if matched.
- **Integrity, balance and resolution.** Missing, empty or altered files;
  dominant classes and thin minorities that make intervals useless; resolution
  outliers, and how many images are downscaled before the model ever sees them.
  Checksum verification is opt-in because it re-reads the whole dataset, and the
  report says which of the two checks ran.
- Database schema 7: `images.split` and `image_features.phash`.

## 0.9.0 — 2026-08-24

Resumable runs. Every stored response was already a checkpoint; nothing knew how
to read them back.

- **Pause and resume.** A run can be paused mid-flight and picked up later. On
  resume only the missing units are executed: a response that reached the model
  is never paid for twice, including an unreadable one, because an unparseable
  answer is a result and not a gap.
- **A restart no longer destroys the run.** Interrupted runs are marked `paused`
  instead of `failed`, with their responses intact. Verified against a real
  qwen3-vl:4b: the process was killed at 67 of 108 responses, and resuming after
  the restart executed the remaining 41 with zero repeated work.
- **Calls that failed are retried, verdicts are not.** A run interrupted by the
  local model server going down resumes and retries exactly the calls that never
  landed.
- **Progress counts stored verdicts, not attempts**, so a run against an
  unreachable model reports the truth instead of a full bar. A run that finishes
  with units still missing says so and stays resumable.
- **Pausing one model pauses the whole Arena batch** rather than skipping ahead
  to the next model, and the queued runs behind it become resumable together.
- Runtime is accumulated across pauses, so the Arena's throughput figures no
  longer charge a model for the hours it spent paused.
- Database schema 6: the `runs` status check is widened to include `paused` and a
  `runtime_seconds` column is added. Existing databases migrate in place with
  their run history and responses preserved.

## 0.8.0 — 2026-08-24

Correctness release. Four defects produced confident numbers that were wrong.

- **Arena ranking is now matched.** Accuracy, intervals, scene balance, fragility,
  format rate and latency are computed on the units every model answered readably.
  A model that returns nothing on the hard images used to win the ranking at 100%
  while the paired test correctly reported no difference; its self-reported figure
  is still shown, labelled, but never ranks.
- **The parser no longer invents verdicts.** An unterminated `<think>` is recorded
  as `truncated` instead of being mined for the first verdict word in the model's
  reasoning; prose naming more than one verdict is `ambiguous` rather than resolved
  by whichever word came first; and the unaccented Italian `si` is no longer read as
  an affirmation, so "non si vede alcuna luce" stops being counted as a yes.
- **Accuracy is split by parser.** The Claim Card and Failure Atlas report how much
  of the headline number arrived as schema-valid JSON and how much was read out of
  prose, plus the units discarded because repetitions split evenly.
- **The loopback server validates the `Host` header**, closing DNS rebinding: a page
  resolving its own domain to 127.0.0.1 could previously read the mutation token
  from `/api/bootstrap` and drive the whole application.
- Ollama calls retry without the `think` switch, so vision models without a thinking
  mode stay usable in the same Arena as a qwen3-vl.
- Runs freeze the variant ids they actually executed, so adding a variant correctly
  marks earlier runs incompatible instead of silently pairing different prompts.
- Balanced accuracy reports `—` instead of sensitivity when one class is absent;
  paired difference intervals no longer collapse to zero width on identical results;
  Arena leader badges are withheld on a tie.
- Failure diagnosis extracts image features in parallel (~30 s serial on 300 images)
  and no longer caches a failed extraction permanently.
- Interface consistency: the dataset fingerprint is computed instead of promised,
  the Evidence Gate shows percentages next to percentage thresholds, the run picker
  marks the synthetic simulator, manual mutations offer the same axes as the
  generator, and the annotation screen no longer calls itself blind.

## 0.7.0 — 2026-08-23

- automatic local downscaling of oversized model inputs to a cached 2048 px JPEG proxy;
- originals remain untouched and exact proxy hashes, dimensions, MIME type and preprocessing profile are recorded per response;
- accuracy now always displays valid/annotated denominators and an explicit low-coverage warning;
- cached proxies are reused across variants, models and later runs without repeated conversion.

## 0.6.0 — 2026-08-23

- prominent project creation control in the persistent top bar;
- recoverable local trash for projects, individual images and complete datasets;
- restore actions that preserve files, annotations and historical run evidence;
- active dataset filtering across imports, annotations, fingerprints, provider tests and new runs.

## 0.5.0 — 2026-08-23

- local Failure Diagnosis with cached thumbnail-derived brightness, contrast, detail and saturation signals;
- risk patterns by visual property, resolution, orientation, source group and mutation axis;
- error-rate deltas, Wilson intervals, representative examples and deterministic failure clusters;
- explicit warnings separating correlation and heuristic visual signals from causal claims.

## 0.4.0 — 2026-08-23

- private local Stress Generator in Mutation Lab;
- controlled generation across paraphrase, negation, ambiguity, language, examples, order, format and length axes;
- editable review queue with explicit approve/save or discard actions;
- text-only Ollama and OpenAI-compatible adapters with private-endpoint enforcement and bounded JSON parsing.

## 0.3.0 — 2026-08-23

- Model Arena for 2–8 matched local models with sequential execution to avoid GPU/VRAM contention;
- automatic grouping and selection of configuration-compatible completed runs;
- contextual ranking with majority-collapsed accuracy, Wilson 95% intervals, scene balance, prompt fragility, repeat drift, format rate and latency;
- all-pairs accuracy deltas, paired confidence intervals, exact McNemar tests and delta matrix;
- explicit common-unit overlap and non-universal-ranking warning;
- visible model catalogue selector after private endpoint discovery;
- confirmed deletion of completed, failed or cancelled runs and their cascaded responses.

## 0.2.0 — 2026-08-23

- Evidence Gate with disclosed maturity checks embedded in UI and Claim Cards;
- scene-balanced accuracy and independent source-group counts;
- deterministic, network-free Synthetic Prompt Stressor for instant demos;
- visible DEMO provenance throughout the run ledger and exported reports;
- one-click provider connection test using a managed local image;
- native dataset and model directory pickers plus private provider model discovery;
- database migration from schema v1 to v2;
- end-to-end UI fixes for asynchronous forms, project selection and view scroll position.

## 0.1.0 — 2026-08-23

- local project and dataset management with SHA-256 deduplication;
- human annotation console;
- controlled prompt mutation lab;
- Ollama and OpenAI-compatible private endpoint adapters;
- Prompt Fragility Score and Repeat Drift separation;
- Wilson intervals, majority baseline and exact McNemar comparisons;
- Failure Atlas, Claim Card and privacy-preserving Replay Bundle;
- loopback-only server and mutation token protection.
