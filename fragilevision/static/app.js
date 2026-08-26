"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
const pct = value => `${(Number(value || 0) * 100).toFixed(1)}%`;
const fmtTime = seconds => seconds ? new Date(seconds * 1000).toLocaleString() : "—";

const F = {token:"", state:{projects:[],providers:[],runs:[]}, project:null, view:"overview",
  annotations:[], annotationIndex:0, annotationQuestionId:0, metrics:null, poll:null,
  annotator:"", annotationBoard:null, agreementStale:false, performance:null, archivedRuns:[],
  arena:null, arenaSelection:[], arenaPending:[], arenaProviderSelection:[], arenaQuestionSelection:[], arenaProjectId:0,
  arenaSelectionTouched:false, stressSuggestions:[], diagnosticRunId:0, audit:null, auditProjectId:0};

async function api(path, options = {}, retryToken = true) {
  const headers = {"Accept":"application/json", ...(options.headers || {})};
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  if (options.method && options.method !== "GET") headers["X-FragileVision-Token"] = F.token;
  const response = await fetch(path, {...options, headers});
  if (response.status === 403 && retryToken && options.method && options.method !== "GET") {
    const bootstrap = await fetch("/api/bootstrap", {headers:{"Accept":"application/json"}});
    if (bootstrap.ok) {
      F.token = (await bootstrap.json()).token;
      return api(path, options, false);
    }
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Errore HTTP ${response.status}`);
  return data;
}

let toastTimer;
function toast(message, error = false) {
  const node = $("#toast"); node.textContent = message; node.className = error ? "show error" : "show";
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.className = "", 3300);
}

function formObject(form) {
  const data = Object.fromEntries(new FormData(form));
  $$('input[type="checkbox"]', form).forEach(input => data[input.name] = input.checked);
  return data;
}

const sectionMeta = {
  overview:["LAB / OVERVIEW","Quanto regge davvero?"], dataset:["DATA / PRIVATE","Costruisci l’evidenza"],
  questions:["PROMPT / MUTATION","Mutation Lab"], annotate:["HUMAN / GROUND TRUTH","Verità di riferimento"],
  providers:["MODELS / LOCAL","Provider verificabili"], runs:["RUNNER / LEDGER","Esecuzioni riproducibili"],
  arena:["MODEL / ARENA","Confronto appaiato"],
  results:["ATLAS / FAILURES","Failure Atlas"],
  performance:["MODEL / COST","Prestazioni"]
};

function go(view) {
  F.view = view;
  window.scrollTo(0, 0);
  $$(".view").forEach(node => node.classList.toggle("active", node.id === `view-${view}`));
  $$(".nav-item").forEach(node => node.classList.toggle("active", node.dataset.view === view));
  $("#section-kicker").textContent = sectionMeta[view][0];
  $("#section-title").textContent = sectionMeta[view][1];
  history.replaceState(null, "", `#${view}`);
  if (view === "annotate") { if (F.annotationQuestionId) loadAnnotations(F.annotationQuestionId); else loadAgreement(); }
  if (view === "arena") renderArenaControls();
  if (view === "performance") loadPerformance();
}

async function boot() {
  const bootstrap = await api("/api/bootstrap");
  F.token = bootstrap.token; F.state = bootstrap.state;
  $("#version").textContent = `FragileVision ${bootstrap.version}`;
  bind(); renderState();
  F.annotator = localStorage.getItem("fv-annotator") || "";
  $("#annotation-annotator").value = F.annotator;
  $("#annotation-blind").checked = localStorage.getItem("fv-blind") !== "0";
  const wanted = location.hash.slice(1);
  if (sectionMeta[wanted]) go(wanted);
  const selected = localStorage.getItem("fv-project");
  const projectId = F.state.projects.some(item => String(item.id) === selected) ? Number(selected) : F.state.projects[0]?.id;
  if (projectId) await selectProject(projectId);
  pollRuns();
  const latest = F.state.runs.find(run => run.status === "completed");
  if (latest) api(`/api/runs/${latest.id}/metrics`).then(metrics => {
    $("#overview-score").textContent = Number(metrics.summary.prompt_fragility_score).toFixed(1);
  }).catch(() => {});
}

function bind() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => go(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => go(button.dataset.go)));
  $("#project-select").addEventListener("change", event => selectProject(Number(event.target.value)));
  $("#new-project-toggle").addEventListener("click", () => $("#project-form").classList.toggle("hidden"));
  $("#top-new-project").addEventListener("click", openNewProjectForm);
  $("#open-trash").addEventListener("click", openTrash);
  $("#close-trash").addEventListener("click", () => $("#trash-dialog").close());
  $("#trash-dataset").addEventListener("click", trashDataset);
  $("#project-form").addEventListener("submit", createProject);
  $("#import-form").addEventListener("submit", importDataset);
  $("#choose-dataset-directory").addEventListener("click", event => chooseDirectory("dataset", $("#import-form").elements.directory, event.currentTarget));
  $("#run-audit").addEventListener("click", runAudit);
  $("#split-form").addEventListener("submit", assignSplit);
  $("#clear-split").addEventListener("click", clearSplit);
  $("#question-form").addEventListener("submit", createQuestion);
  $("#question-edit-form").addEventListener("submit", updateQuestion);
  $("#stress-form").addEventListener("submit", generateStressVariants);
  $("#provider-form").addEventListener("submit", createProvider);
  $("#choose-model-directory").addEventListener("click", event => chooseDirectory("model", $("#provider-form").elements.model, event.currentTarget));
  $("#discover-models").addEventListener("click", discoverProviderModels);
  $("#detected-models").addEventListener("change", selectDetectedModel);
  $("#provider-form").elements.kind.addEventListener("change", clearDetectedModels);
  $("#provider-form").elements.endpoint.addEventListener("input", clearDetectedModels);
  $("#add-simulator").addEventListener("click", createDemoProvider);
  $("#run-form").addEventListener("submit", createRun);
  $("#run-rename-form").addEventListener("submit", submitRunRename);
  $("#run-rename-cancel").addEventListener("click", () => $("#run-rename-dialog").close());
  $("#run-duplicate-form").addEventListener("submit", submitRunDuplicate);
  $("#run-duplicate-cancel").addEventListener("click", () => $("#run-duplicate-dialog").close());
  $$("#run-filter-project, #run-filter-status, #run-filter-provider").forEach(
    select => select.addEventListener("change", renderRuns));
  $("#run-filter-search").addEventListener("input", renderRuns);
  $("#run-filter-archived").addEventListener("change", loadRuns);
  $("#arena-form").addEventListener("submit", createArenaRuns);
  $("#arena-compare").addEventListener("click", () => compareArena());
  $("#arena-providers").addEventListener("change", () => { F.arenaProviderSelection = $$('#arena-providers input:checked').map(input => Number(input.value)); });
  $("#arena-questions").addEventListener("change", () => { F.arenaQuestionSelection = $$('#arena-questions input:checked').map(input => Number(input.value)); });
  $("#arena-run-list").addEventListener("change", updateArenaSelection);
  $("#annotation-question").addEventListener("change", event => loadAnnotations(Number(event.target.value)));
  $("#annotation-annotator").addEventListener("change", event => setAnnotator(event.target.value));
  $("#annotation-blind").addEventListener("change", event => {
    localStorage.setItem("fv-blind", event.target.checked ? "1" : "0"); showAnnotation();
  });
  $("#annotation-withdraw").addEventListener("click", withdrawAnnotation);
  $("#refresh-agreement").addEventListener("click", loadAgreement);
  $("#annotation-prev").addEventListener("click", () => moveAnnotation(-1));
  $("#annotation-next").addEventListener("click", () => moveAnnotation(1));
  $$('[data-verdict]').forEach(button => button.addEventListener("click", () => saveAnnotation(button.dataset.verdict)));
  $("#result-run").addEventListener("change", event => loadResults(Number(event.target.value)));
  $("#refresh-performance").addEventListener("click", loadPerformance);
  $("#variant-form").addEventListener("submit", createVariant);
  document.addEventListener("click", event => {
    const trashProjectButton = event.target.closest("[data-trash-project]"); if (trashProjectButton) { trashProject(Number(trashProjectButton.dataset.trashProject)); return; }
    const trashImageButton = event.target.closest("[data-trash-image]"); if (trashImageButton) { trashImage(Number(trashImageButton.dataset.trashImage)); return; }
    const restoreProjectButton = event.target.closest("[data-restore-project]"); if (restoreProjectButton) { restoreProject(Number(restoreProjectButton.dataset.restoreProject)); return; }
    const restoreImageButton = event.target.closest("[data-restore-image]"); if (restoreImageButton) { restoreImage(Number(restoreImageButton.dataset.restoreImage)); return; }
    const card = event.target.closest("[data-project]"); if (card) selectProject(Number(card.dataset.project));
    const add = event.target.closest("[data-add-variant]"); if (add) openVariantDialog(Number(add.dataset.addVariant));
    const editQuestion = event.target.closest("[data-edit-question]"); if (editQuestion) openQuestionEditor(Number(editQuestion.dataset.editQuestion));
    const testProviderButton = event.target.closest("[data-test-provider]"); if (testProviderButton) testProvider(Number(testProviderButton.dataset.testProvider), testProviderButton);
    const deleteProviderButton = event.target.closest("[data-delete-provider]"); if (deleteProviderButton) deleteProvider(Number(deleteProviderButton.dataset.deleteProvider));
    const result = event.target.closest("[data-result-run]"); if (result) { go("results"); $("#result-run").value = result.dataset.resultRun; loadResults(Number(result.dataset.resultRun)); }
    const cancel = event.target.closest("[data-cancel-run]"); if (cancel) cancelRun(Number(cancel.dataset.cancelRun));
    const pause = event.target.closest("[data-pause-run]"); if (pause) pauseRun(Number(pause.dataset.pauseRun));
    const resume = event.target.closest("[data-resume-run]"); if (resume) resumeRun(Number(resume.dataset.resumeRun), resume);
    const remove = event.target.closest("[data-delete-run]"); if (remove) deleteRun(Number(remove.dataset.deleteRun));
    const rename = event.target.closest("[data-rename-run]"); if (rename) openRunRename(Number(rename.dataset.renameRun));
    const duplicate = event.target.closest("[data-duplicate-run]"); if (duplicate) openRunDuplicate(Number(duplicate.dataset.duplicateRun));
    const archiveRun = event.target.closest("[data-archive-run]"); if (archiveRun) runArchiveAction(Number(archiveRun.dataset.archiveRun), false);
    const unarchiveRun = event.target.closest("[data-unarchive-run]"); if (unarchiveRun) runArchiveAction(Number(unarchiveRun.dataset.unarchiveRun), true);
    const arbitrate = event.target.closest("[data-adjudicate]");
    if (arbitrate) { const [image, question, value] = arbitrate.dataset.adjudicate.split(":"); adjudicate(Number(image), Number(question), value); return; }
    const probeMemory = event.target.closest("[data-probe-memory]"); if (probeMemory) { probeMemory.disabled = true; probeMemoryFor(Number(probeMemory.dataset.probeMemory), probeMemory); }
    const freeMemory = event.target.closest("[data-free-memory]"); if (freeMemory) { freeMemory.disabled = true; freeMemoryFor(Number(freeMemory.dataset.freeMemory), freeMemory); }
    const saveStress = event.target.closest("[data-save-stress]"); if (saveStress) saveStressVariant(Number(saveStress.dataset.saveStress));
    const discardStress = event.target.closest("[data-discard-stress]"); if (discardStress) { F.stressSuggestions.splice(Number(discardStress.dataset.discardStress),1); renderStressSuggestions(); }
  });
  document.addEventListener("keydown", event => {
    if (F.view !== "annotate" || ["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName)) return;
    const verdict = {"1":"yes","2":"no","3":"uncertain","4":"exclude"}[event.key];
    if (verdict) { event.preventDefault(); saveAnnotation(verdict); }
    if (event.key === "ArrowLeft") moveAnnotation(-1);
    if (event.key === "ArrowRight") moveAnnotation(1);
  });
}

function renderState() {
  const projects = F.state.projects, providers = F.state.providers, runs = F.state.runs;
  $("#project-select").innerHTML = '<option value="">Nessun progetto</option>' + projects.map(project => `<option value="${project.id}">${esc(project.name)}</option>`).join("");
  if (F.project) $("#project-select").value = String(F.project.id);
  $("#project-cards").className = projects.length ? "project-cards" : "project-cards empty-state";
  $("#project-cards").innerHTML = projects.length ? projects.map(project => `<div class="project-card ${F.project?.id===project.id?'active':''}" data-project="${project.id}"><div><b>${esc(project.name)}</b><small>${project.image_count} immagini · ${project.question_count} domande</small></div><div class="project-card-actions"><code>${esc(project.slug)}</code><button class="mini danger-action" type="button" data-trash-project="${project.id}">Rimuovi</button></div></div>`).join("") : "Nessun progetto. Crea il primo laboratorio.";
  $("#provider-count").textContent = providers.length;
  $("#provider-list").className = providers.length ? "provider-list" : "provider-list empty-state";
  $("#provider-list").innerHTML = providers.length ? providers.map(provider => {
    const canFreeMemory = provider.kind === "ollama" && !provider.is_demo;
    return `<div class="provider-card"><header><b>${esc(provider.name)}</b><span class="status ${provider.is_demo?'demo':''}">${provider.is_demo?'DEMO · OFFLINE':esc(provider.kind)}</span></header><code>${esc(provider.model)}${provider.is_demo?' · nessuna rete':` · ${esc(provider.endpoint)}`}</code><div class="provider-card-actions"><button class="mini" data-test-provider="${provider.id}">Test connessione</button>${canFreeMemory ? `<button class="mini" type="button" data-free-memory="${provider.id}">Libera memoria adesso</button>` : ""}<button class="mini danger" data-delete-provider="${provider.id}">Elimina</button></div></div>`;
  }).join("") : "Nessun modello configurato.";
  $("#run-provider").innerHTML = '<option value="">Seleziona</option>' + providers.map(provider => `<option value="${provider.id}">${esc(provider.name)} · ${esc(provider.model)}${provider.is_demo?' · DEMO — non è un modello visivo':''}</option>`).join("");
  renderRuns(); renderArenaControls(); renderStressControls();
}

async function refreshState() {
  F.state = await api("/api/state"); renderState();
  if (F.project) await selectProject(F.project.id, false);
}

async function selectProject(id, remember = true) {
  if (!id) { F.project = null; renderProject(); return; }
  if (F.arenaProjectId && F.arenaProjectId !== id) {
    F.arenaSelection = []; F.arenaPending = []; F.arenaQuestionSelection = []; F.arena = null; F.arenaSelectionTouched = false;
    $("#arena-results")?.classList.add("hidden"); $("#arena-empty")?.classList.remove("hidden");
  }
  if (F.auditProjectId && F.auditProjectId !== id) {
    F.audit = null; $("#audit-results")?.classList.add("hidden"); $("#audit-empty")?.classList.remove("hidden");
    $("#split-state").textContent = "non assegnata"; $("#split-summary").innerHTML = "";
  }
  F.auditProjectId = id;
  F.project = await api(`/api/projects/${id}`);
  F.arenaProjectId = id;
  api(`/api/projects/${id}/fingerprint`).then(result => {
    if (F.project?.id === id) $("#dataset-fingerprint").textContent = result.fingerprint.slice(0, 32);
  }).catch(() => { $("#dataset-fingerprint").textContent = "non calcolabile"; });
  if (remember) localStorage.setItem("fv-project", String(id));
  $("#project-select").value = String(id); renderProject();
}

function renderProject() {
  const project = F.project;
  $$(".project-card").forEach(card => card.classList.toggle("active", Number(card.dataset.project) === project?.id));
  if (!project) {
    ["#stat-images","#stat-questions","#stat-annotations","#stat-runs","#health-images","#gallery-count"].forEach(selector => $(selector).textContent = "0");
    $("#health-groups").textContent = "—"; $("#health-coverage").textContent = "0%";
    $("#image-gallery").className = "image-gallery empty-state"; $("#image-gallery").textContent = "Seleziona o crea un progetto.";
    $("#dataset-fingerprint").textContent = "nessun progetto selezionato";
    $("#trash-dataset").disabled = true;
    return;
  }
  const stateProject = F.state.projects.find(item => item.id === project.id) || {};
  $("#stat-images").textContent = project.images.length;
  $("#stat-questions").textContent = project.questions.length;
  $("#stat-annotations").textContent = stateProject.annotation_count || 0;
  $("#stat-runs").textContent = F.state.runs.filter(run => run.project_id === project.id).length;
  $("#health-images").textContent = project.images.length;
  const groups = new Set(project.images.map(image => image.source_group).filter(Boolean));
  $("#health-groups").textContent = groups.size || "—";
  const possible = project.images.length * project.questions.length;
  $("#health-coverage").textContent = possible ? pct((stateProject.annotation_count || 0) / possible) : "0%";
  $("#gallery-count").textContent = project.images.length;
  $("#image-gallery").className = project.images.length ? "image-gallery" : "image-gallery empty-state";
  $("#image-gallery").innerHTML = project.images.length ? project.images.map(image => `<div class="image-card"><img loading="lazy" src="/media/${image.id}" alt=""><span>${esc(image.filename)}</span><button class="image-remove" type="button" data-trash-image="${image.id}" aria-label="Rimuovi ${esc(image.filename)}">×</button></div>`).join("") : "Importa una cartella per iniziare.";
  $("#trash-dataset").disabled = !project.images.length;
  renderQuestions(); renderAnnotationQuestions(); renderRunQuestions(); renderArenaControls(); renderStressControls();
}

async function createProject(event) {
  event.preventDefault(); const form = event.currentTarget;
  try { const project = await api("/api/projects", {method:"POST", body:formObject(form)}); form.reset(); $("#project-form").classList.add("hidden"); await refreshState(); await selectProject(project.id); toast("Laboratorio creato"); }
  catch (error) { toast(error.message, true); }
}

function openNewProjectForm() {
  go("overview");
  $("#project-form").classList.remove("hidden");
  requestAnimationFrame(() => $("#project-form").elements.name.focus());
}

async function openTrash() {
  try {
    const contents = await api("/api/trash");
    $("#trash-projects").className = contents.projects.length ? "trash-list" : "trash-list empty-state";
    $("#trash-projects").innerHTML = contents.projects.length ? contents.projects.map(project => `<div class="trash-row"><div><b>${esc(project.name)}</b><small>${project.image_count} immagini · rimosso ${esc(fmtTime(project.deleted_at))}</small></div><button class="mini" type="button" data-restore-project="${project.id}">Ripristina</button></div>`).join("") : "Nessun progetto rimosso.";
    $("#trash-images").className = contents.images.length ? "trash-list" : "trash-list empty-state";
    $("#trash-images").innerHTML = contents.images.length ? contents.images.map(image => `<div class="trash-row"><div><b>${esc(image.filename)}</b><small>${esc(image.project_name)} · rimosso ${esc(fmtTime(image.deleted_at))}</small></div><button class="mini" type="button" data-restore-image="${image.id}">Ripristina</button></div>`).join("") : "Nessuna immagine rimossa.";
    if (!$("#trash-dialog").open) $("#trash-dialog").showModal();
  } catch (error) { toast(error.message, true); }
}

async function trashImage(id) {
  const image = F.project?.images.find(item => item.id === id);
  if (!image || !window.confirm(`Rimuovere “${image.filename}” dal dataset? Potrai ripristinarla dal Cestino.`)) return;
  try { await api(`/api/images/${id}/trash`, {method:"POST",body:{}}); await refreshState(); toast("Immagine spostata nel Cestino"); }
  catch (error) { toast(error.message, true); }
}

async function trashDataset() {
  if (!F.project?.images.length || !window.confirm(`Rimuovere tutte le ${F.project.images.length} immagini da “${F.project.name}”? File e storico resteranno recuperabili.`)) return;
  try { await api(`/api/projects/${F.project.id}/trash-images`, {method:"POST",body:{}}); await refreshState(); toast("Dataset spostato nel Cestino"); }
  catch (error) { toast(error.message, true); }
}

async function trashProject(id) {
  const project = F.state.projects.find(item => item.id === id);
  if (!project || !window.confirm(`Rimuovere il progetto “${project.name}”? Potrai ripristinarlo dal Cestino.`)) return;
  try {
    await api(`/api/projects/${id}/trash`, {method:"POST",body:{}});
    if (F.project?.id === id) { F.project = null; localStorage.removeItem("fv-project"); }
    await refreshState();
    if (!F.project && F.state.projects[0]) await selectProject(F.state.projects[0].id);
    else if (!F.project) renderProject();
    toast("Progetto spostato nel Cestino");
  } catch (error) { toast(error.message, true); }
}

async function restoreProject(id) {
  try { await api(`/api/projects/${id}/restore`, {method:"POST",body:{}}); await refreshState(); await openTrash(); toast("Progetto ripristinato"); }
  catch (error) { toast(error.message, true); }
}

async function restoreImage(id) {
  try { await api(`/api/images/${id}/restore`, {method:"POST",body:{}}); await refreshState(); await openTrash(); toast("Immagine ripristinata"); }
  catch (error) { toast(error.message, true); }
}

async function importDataset(event) {
  event.preventDefault(); const form = event.currentTarget, button = $("button[type=submit]", form); button.disabled = true; button.textContent = "Indicizzazione…";
  try { await ensureProjectForImport(form.elements.directory.value); const result = await api(`/api/projects/${F.project.id}/import`, {method:"POST", body:formObject(form)}); $("#import-result").textContent = `${result.imported} importate · ${result.duplicates} duplicate · ${result.rejected.length} rifiutate`; await refreshState(); toast("Dataset aggiornato"); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Importa in locale"; }
}

async function ensureProjectForImport(directory) {
  if (F.project) return F.project;
  if (F.state.projects.length) { await selectProject(F.state.projects[0].id); return F.project; }
  const parts = String(directory).split(/[\\/]/).filter(Boolean), name = parts.at(-1) || "Dataset locale";
  const project = await api("/api/projects", {method:"POST", body:{name,description:"Laboratorio creato automaticamente durante l’importazione locale."}});
  await refreshState(); await selectProject(project.id); toast(`Creato automaticamente: ${project.name}`); return F.project;
}

async function chooseDirectory(purpose, input, button) {
  const original = button.textContent; button.disabled = true; button.textContent = "Scelta in corso…";
  try {
    const result = await api("/api/system/choose-directory", {method:"POST", body:{purpose}});
    if (result.directory) { input.value = result.directory; input.dispatchEvent(new Event("change", {bubbles:true})); toast("Cartella selezionata"); }
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

function renderAudit(audit) {
  $("#audit-empty").classList.add("hidden"); $("#audit-results").classList.remove("hidden");
  const integrity = audit.integrity, dupes = audit.near_duplicates, groups = audit.groups, resolution = audit.resolution;
  $("#audit-warnings").innerHTML = audit.warnings.length
    ? audit.warnings.map(item => `<div class="audit-warning ${esc(item.severity)}"><i>${esc(item.severity)}</i><span><b>${esc(item.area)}</b> — ${esc(item.text)}</span></div>`).join("")
    : '<div class="audit-warning bassa"><i>ok</i><span>Nessun problema rilevato con i controlli disponibili.</span></div>';
  const rotte = integrity.missing_count + integrity.unreadable_count + integrity.changed_count;
  $("#audit-integrity").textContent = `${integrity.healthy}/${integrity.checked}`;
  $("#audit-integrity-note").textContent = rotte ? `${rotte} da controllare` : (integrity.checksums_verified ? "checksum verificati" : "checksum non verificati");
  $("#audit-duplicates").textContent = dupes.scanned ? dupes.pair_count : "—";
  $("#audit-duplicates-note").textContent = dupes.scanned
    ? `${dupes.identical_pairs} identiche · ${dupes.cross_group_pairs} fra gruppi diversi · soglia ${dupes.threshold} bit`
    : esc(dupes.reason || "non eseguito");
  $("#audit-groups").textContent = groups.count;
  $("#audit-groups-note").textContent = `il più grande copre ${pct(groups.largest_share)}${groups.ungrouped ? ` · ${groups.ungrouped} senza gruppo` : ""}`;
  $("#audit-resolution").textContent = resolution.measured ? `${resolution.median_long_edge} px` : "—";
  $("#audit-resolution-note").textContent = resolution.measured
    ? `${resolution.outlier_count} anomale · ${resolution.downscaled_for_model} ridotte a ${resolution.model_input_max_edge} px per il modello` : "";
  $("#audit-duplicate-pairs").innerHTML = (dupes.pairs || []).slice(0, 12).map(pair => `<article class="duplicate-pair ${pair.same_group?"":"cross"}"><header><b>${esc(pair.kind)}</b><span>${pair.distance} bit di scarto</span></header><figure><img loading="lazy" src="/media/${pair.a_id}" alt=""><img loading="lazy" src="/media/${pair.b_id}" alt=""></figure><code>${esc(pair.a_filename)}</code><code>${esc(pair.b_filename)}</code>${pair.same_group?`<code>gruppo “${esc(pair.a_group)}”</code>`:`<code class="warning">gruppi diversi: “${esc(pair.a_group)}” e “${esc(pair.b_group)}”</code>`}</article>`).join("");
  $("#audit-balance").innerHTML = audit.balance.length
    ? `<table><thead><tr><th>Domanda</th><th>Sì</th><th>No</th><th>Incerto</th><th>Escluso</th><th>Non annotate</th><th>Classe maggioritaria</th></tr></thead><tbody>${audit.balance.map(item => `<tr><td><b>${esc(item.label)}</b><small class="table-sub">${esc(item.key)}</small></td><td>${item.counts.yes}</td><td>${item.counts.no}</td><td>${item.counts.uncertain}</td><td>${item.counts.exclude}</td><td class="${item.unannotated?"delta-negative":""}">${item.unannotated}</td><td class="${item.majority_share>.8?"delta-negative":""}">${item.decidable?pct(item.majority_share):"—"}</td></tr>`).join("")}</tbody></table>`
    : '<div class="empty-state">Nessuna domanda definita.</div>';
  $("#audit-limitations").textContent = audit.limitations;
  renderSplitSummary(audit.split, dupes);
  if (audit.fingerprint) $("#dataset-fingerprint").textContent = audit.fingerprint.slice(0, 32);
}

function renderSplitSummary(split, dupes) {
  const assegnata = split.assigned > 0;
  $("#split-state").textContent = assegnata ? `${split.train} train · ${split.test} test` : "non assegnata";
  $("#split-summary").innerHTML = assegnata
    ? `<div><b>${split.train}</b><span>TRAIN</span></div><div><b>${split.test}</b><span>TEST</span></div><div><b>${split.unassigned}</b><span>NON ASSEGNATE</span></div><div><b class="${split.leak_count?"warning":""}">${split.leak_count}</b><span>QUASI DUPLICATI DIVISI</span></div>${split.leak_count?`<div class="warning">Il test sta misurando su immagini che il train già contiene: riassegna la suddivisione.</div>`:""}`
    : `<div class="empty-state">Nessuna suddivisione assegnata. ${dupes && dupes.scanned ? `Il controllo ha trovato ${dupes.pair_count} coppie quasi identiche che verranno tenute insieme.` : ""}</div>`;
}

async function runAudit() {
  if (!F.project) return toast("Seleziona prima un progetto", true);
  const button = $("#run-audit"), original = button.textContent, deep = $("#audit-deep").checked;
  button.disabled = true; button.textContent = deep ? "Verifica completa…" : "Controllo…";
  try {
    F.audit = await api(`/api/projects/${F.project.id}/audit${deep ? "?deep=1" : ""}`);
    renderAudit(F.audit);
    const gravi = F.audit.warnings.filter(item => item.severity === "alta").length;
    toast(gravi ? `${gravi} problemi da guardare prima di eseguire` : "Nessun problema grave rilevato", gravi > 0);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

async function assignSplit(event) {
  event.preventDefault(); if (!F.project) return toast("Seleziona prima un progetto", true);
  const form = event.currentTarget, button = $("button[type=submit]", form);
  button.disabled = true;
  try {
    const result = await api(`/api/projects/${F.project.id}/split`, {method:"POST", body:{
      seed: Number(form.elements.seed.value), test_ratio: Number(form.elements.test_ratio.value)}});
    toast(`${result.train} train · ${result.test} test su ${result.clusters} scene indipendenti (richiesto ${pct(result.requested_ratio)}, ottenuto ${pct(result.achieved_ratio)})`);
    await selectProject(F.project.id, false); await runAudit();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function clearSplit() {
  if (!F.project || !window.confirm("Azzerare la suddivisione train/test di questo progetto?")) return;
  try {
    await api(`/api/projects/${F.project.id}/split`, {method:"POST", body:{clear:true}});
    await selectProject(F.project.id, false); await runAudit(); toast("Suddivisione azzerata");
  } catch (error) { toast(error.message, true); }
}

async function createQuestion(event) {
  event.preventDefault(); if (!F.project) return toast("Seleziona prima un progetto", true); const form = event.currentTarget;
  try { await api(`/api/projects/${F.project.id}/questions`, {method:"POST", body:formObject(form)}); form.reset(); form.elements.language.value = "it"; await refreshState(); toast("Domanda canonica registrata"); }
  catch (error) { toast(error.message, true); }
}

const mutationLabel = type => ({canonical:"canonica",language:"lingua",negation:"negazione",ambiguity:"ambiguità",paraphrase:"riformulazione",examples:"esempi",order:"ordine",format:"formato",length:"lunghezza",manual:"manuale"}[type] || type);
function renderQuestions() {
  const questions = F.project?.questions || [];
  $("#question-list").className = questions.length ? "question-list" : "question-list empty-state";
  $("#question-list").innerHTML = questions.length ? questions.map(question => `<article class="question-card"><div class="question-main"><div><span class="question-key">${esc(question.key)}</span><h3>${esc(question.label)}</h3></div><p>${esc(question.description || "Nessuna descrizione del costrutto.")}</p><div class="question-actions"><button class="mini" data-edit-question="${question.id}">Modifica</button><button class="mini" data-add-variant="${question.id}">＋ Mutazione</button></div></div><div class="variant-list">${question.variants.map(variant => `<div class="variant-row"><b>${esc(variant.name)}</b><code>${esc(variant.language)}</code><code>${esc(mutationLabel(variant.mutation_type))}</code><p>${esc(variant.text)}</p></div>`).join("")}</div></article>`).join("") : "Crea una domanda per aprire il Mutation Lab.";
}

function openQuestionEditor(questionId) {
  const question=F.project?.questions.find(item=>item.id===questionId); if(!question) return;
  const canonical=question.variants.find(variant=>variant.canonical); if(!canonical) return toast("Variante canonica non trovata",true);
  const form=$("#question-edit-form"); form.elements.question_id.value=question.id; form.elements.label.value=question.label;
  form.elements.key.value=question.key; form.elements.description.value=question.description||"";
  form.elements.text.value=canonical.text; form.elements.language.value=canonical.language||"it";
  $("#question-edit-dialog").showModal();
}

async function updateQuestion(event) {
  event.preventDefault(); const form=event.currentTarget,data=formObject(form),questionId=Number(data.question_id); delete data.question_id;
  try { await api(`/api/questions/${questionId}/edit`,{method:"POST",body:data}); $("#question-edit-dialog").close(); await selectProject(F.project.id,false); toast("Domanda canonica aggiornata"); }
  catch(error){ toast(error.message,true); }
}

function openVariantDialog(questionId) {
  const question = F.project?.questions.find(item => item.id === questionId); if (!question) return;
  const form = $("#variant-form"); form.reset(); form.question_id.value = questionId; form.language.value = question.variants[0]?.language || "it"; form.text.value = question.variants[0]?.text || ""; $("#variant-dialog").showModal();
}

function renderStressControls() {
  const questions = F.project?.questions || [], providers = F.state.providers || [];
  const questionSelect = $("#stress-question"), providerSelect = $("#stress-provider");
  const oldQuestion = questionSelect.value, oldProvider = providerSelect.value;
  questionSelect.innerHTML = '<option value="">Seleziona</option>' + questions.map(question => `<option value="${question.id}">${esc(question.label)}</option>`).join("");
  providerSelect.innerHTML = '<option value="">Seleziona</option>' + providers.map(provider => `<option value="${provider.id}">${esc(provider.name)} · ${esc(provider.model)}${provider.is_demo?' · DEMO':''}</option>`).join("");
  if (questions.some(item => String(item.id) === oldQuestion)) questionSelect.value = oldQuestion;
  else if (questions.length === 1) questionSelect.value = String(questions[0].id);
  if (providers.some(item => String(item.id) === oldProvider)) providerSelect.value = oldProvider;
  else if (providers.length === 1) providerSelect.value = String(providers[0].id);
}

async function generateStressVariants(event) {
  event.preventDefault(); const form = event.currentTarget, button = $("#stress-generate");
  const questionId = Number(form.elements.question_id.value), providerId = Number(form.elements.provider_id.value);
  const axes = $$('input[name="axis"]:checked', form).map(input => input.value);
  if (!questionId || !providerId) return toast("Seleziona domanda e modello locale", true);
  if (!axes.length) return toast("Seleziona almeno un asse di stress", true);
  button.disabled = true; button.textContent = "Generazione locale…";
  try {
    const result = await api(`/api/questions/${questionId}/generate-variants`, {method:"POST",body:{provider_id:providerId,axes,language:form.elements.language.value}});
    F.stressSuggestions = result.variants.map(item => ({...item,question_id:questionId})); renderStressSuggestions();
    toast(`${result.variants.length} proposte pronte per la revisione`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Genera con il modello locale"; }
}

function renderStressSuggestions() {
  const root = $("#stress-results");
  root.innerHTML = F.stressSuggestions.map((item,index) => `<article class="stress-card" data-stress-card="${index}"><header><span>${esc(mutationLabel(item.axis))}</span><button class="mini" type="button" data-discard-stress="${index}">Scarta</button></header><div class="form-row"><label>Nome<input data-field="name" value="${esc(item.name)}"></label><label>Lingua<input data-field="language" value="${esc(item.language)}" maxlength="12"></label></div><label>Testo revisionabile<textarea data-field="text" rows="3">${esc(item.text)}</textarea></label><button class="primary" type="button" data-save-stress="${index}">Approva e salva</button></article>`).join("");
}

async function saveStressVariant(index) {
  const item = F.stressSuggestions[index], card = $(`[data-stress-card="${index}"]`); if (!item || !card) return;
  const button = $("[data-save-stress]", card); button.disabled = true;
  try {
    await api(`/api/questions/${item.question_id}/variants`, {method:"POST",body:{name:$('[data-field="name"]',card).value,language:$('[data-field="language"]',card).value,text:$('[data-field="text"]',card).value,mutation_type:item.axis}});
    F.stressSuggestions.splice(index,1); renderStressSuggestions(); await selectProject(F.project.id,false); toast("Variante approvata e salvata");
  } catch (error) { button.disabled = false; toast(error.message,true); }
}

async function createVariant(event) {
  event.preventDefault(); const data = formObject(event.currentTarget); const questionId = Number(data.question_id); delete data.question_id;
  try { await api(`/api/questions/${questionId}/variants`, {method:"POST", body:data}); $("#variant-dialog").close(); await selectProject(F.project.id, false); toast("Mutazione registrata"); }
  catch (error) { toast(error.message, true); }
}

const VERDICT_LABELS = {yes:"Sì", no:"No", uncertain:"Incerto", exclude:"Escluso"};
const AGREEMENT_LABELS = {single:"un solo giudizio", unanimous:"unanime", majority:"maggioranza",
  conflict:"conflitto", adjudicated:"arbitrato"};
const coef = value => value === null || value === undefined ? "—" : Number(value).toFixed(3);
const pctOrDash = value => value === null || value === undefined ? "—" : pct(value);

function renderAnnotationQuestions() {
  const questions = F.project?.questions || [];
  $("#annotation-question").innerHTML = '<option value="">Seleziona</option>' + questions.map(question => `<option value="${question.id}">${esc(question.label)}</option>`).join("");
  if (questions.some(question => question.id === F.annotationQuestionId)) $("#annotation-question").value = String(F.annotationQuestionId);
  $("#annotator-names").innerHTML = (F.project?.annotators || []).map(item => `<option value="${esc(item.annotator)}">`).join("");
}

function annotatorName() { return ($("#annotation-annotator").value || "").trim(); }

// Labels arrive as JSON, so the reviewer's own row is a copy and never the same
// object as `item.mine`: identity comparison would show one judgement twice.
function isMine(label) {
  return !label.is_adjudication && String(label.annotator || "").toLowerCase() === (F.annotator || "").toLowerCase();
}

function setAnnotator(name) {
  F.annotator = String(name || "").trim();
  localStorage.setItem("fv-annotator", F.annotator);
  return F.annotationQuestionId ? loadAnnotations(F.annotationQuestionId) : Promise.resolve();
}

async function ensureAnnotator() {
  const typed = annotatorName();
  if (!typed) { toast("Scrivi il tuo nome nel campo “Revisore”", true); $("#annotation-annotator").focus(); return null; }
  if (typed.toLowerCase() !== (F.annotator || "").toLowerCase()) {
    // Whoever is at the keyboard changed. Attributing this click to the reviewer
    // who was loaded a second ago would put one person's judgement under another
    // person's name, which is the one error the whole panel exists to prevent.
    await setAnnotator(typed);
    toast(`Ora annoti come ${typed}: la coda riparte dal tuo primo caso`);
    return null;
  }
  return typed;
}

async function loadAnnotations(questionId) {
  if (!questionId || !F.project) return;
  F.annotationQuestionId = questionId;
  const query = `question_id=${questionId}&annotator=${encodeURIComponent(F.annotator || "")}`;
  const board = await api(`/api/projects/${F.project.id}/annotations?${query}`);
  F.annotations = board.annotations; F.annotationBoard = board;
  // The queue starts at the first case *this* reviewer has not judged: a second
  // reviewer opening a fully annotated dataset must still have work to do.
  const pending = F.annotations.findIndex(item => !(F.annotator ? item.mine : item.value));
  F.annotationIndex = pending >= 0 ? pending : 0;
  showAnnotation();
  loadAgreement();
}

function consensusChip(item) {
  if (!item.value) return '<i class="chip none">nessun giudizio</i>';
  const state = item.agreement || "single";
  const detail = state === "adjudicated" ? esc(item.adjudicated_by || "")
    : state === "single" ? "" : `${item.label_count} giudizi`;
  return `<i class="chip ${esc(state)}">${AGREEMENT_LABELS[state] || state}${detail ? ` · ${detail}` : ""}</i>`
    + `<i class="chip value ${esc(item.value)}">consenso: ${VERDICT_LABELS[item.value] || item.value}</i>`;
}

function renderConsensusStrip(item) {
  const mine = item.mine;
  const others = (item.labels || []).filter(label => !label.is_adjudication && !isMine(label));
  if ($("#annotation-blind").checked && !mine) {
    // Blind means blind: showing the consensus, or even how it was reached,
    // anchors the judgement this case is here to collect independently.
    $("#annotation-consensus").innerHTML = others.length
      ? `<i class="chip blind">${others.length === 1 ? "1 giudizio nascosto" : `${others.length} giudizi nascosti`} finché non voti</i>`
      : '<i class="chip none">nessun altro giudizio</i>';
    return;
  }
  const own = mine ? `<i class="chip mine ${esc(mine.value)}">tu: ${VERDICT_LABELS[mine.value] || mine.value}</i>` : "";
  const peers = others.map(label => `<i class="chip peer ${esc(label.value)}" title="${esc(label.note || "")}">${esc(label.annotator)}: ${VERDICT_LABELS[label.value] || label.value}</i>`).join("");
  $("#annotation-consensus").innerHTML = own + peers + consensusChip(item);
}

function showAnnotation() {
  const item = F.annotations[F.annotationIndex], question = F.project?.questions.find(q => q.id === F.annotationQuestionId);
  if (!item || !question) { $("#annotation-stage").classList.add("empty"); return; }
  $("#annotation-stage").classList.remove("empty"); $("#annotation-image").src = `/media/${item.image_id}`;
  $("#annotation-index").textContent = `${F.annotationIndex + 1} / ${F.annotations.length}`;
  $("#annotation-prompt").textContent = question.variants.find(v => v.canonical)?.text || question.label;
  $("#annotation-file").textContent = item.filename;
  // The note field belongs to the reviewer, not to the case: showing the panel's
  // merged notes here would let one person overwrite another's reasoning.
  $("#annotation-note").value = (item.mine ? item.mine.note : "") || "";
  const shown = item.mine ? item.mine.value : ($("#annotation-blind").checked ? "" : item.value);
  $$('[data-verdict]').forEach(button => button.classList.toggle("selected", button.dataset.verdict === shown));
  $("#annotation-withdraw").classList.toggle("hidden", !item.mine);
  renderConsensusStrip(item);
  const mineDone = F.annotations.filter(row => row.mine).length;
  const anyDone = F.annotations.filter(row => row.value).length;
  const done = F.annotator ? mineDone : anyDone;
  $("#annotation-progress-label").textContent = F.annotator
    ? `${done} / ${F.annotations.length} annotate da ${F.annotator}`
    : `${done} / ${F.annotations.length} annotate`;
  $("#annotation-progress-bar").style.width = `${F.annotations.length ? 100 * done / F.annotations.length : 0}%`;
  const consensus = F.annotationBoard?.consensus;
  $("#annotation-panel-label").textContent = F.annotator
    ? (consensus ? `${consensus.verified} casi con più revisori · ${consensus.open_conflicts} conflitti aperti` : "")
    : "scrivi il tuo nome nel campo “Revisore” per annotare";
}

function moveAnnotation(delta) { if (!F.annotations.length) return; F.annotationIndex = Math.max(0, Math.min(F.annotations.length - 1, F.annotationIndex + delta)); showAnnotation(); }

function applyConsensus(item, consensus) {
  item.value = consensus ? consensus.value : null;
  item.agreement = consensus ? consensus.agreement : null;
  item.label_count = consensus ? consensus.label_count : 0;
  item.adjudicated_by = consensus ? consensus.adjudicated_by : null;
}

async function saveAnnotation(value) {
  if (!F.annotations[F.annotationIndex]) return;
  const annotator = await ensureAnnotator(); if (!annotator) return;
  const item = F.annotations[F.annotationIndex]; if (!item) return;
  const note = $("#annotation-note").value;
  try {
    const result = await api("/api/annotations", {method:"POST", body:{image_id:item.image_id,
      question_id:F.annotationQuestionId, value, note, annotator}});
    const mine = {annotator:result.annotator, value, note, is_adjudication:false, updated_at:Date.now()/1000};
    item.labels = (item.labels || []).filter(label => label.is_adjudication || !isMine(label)).concat([mine]);
    item.mine = mine;
    applyConsensus(item, result.consensus);
    markAgreementStale();
    showAnnotation();
    const next = F.annotations.findIndex((row, index) => index > F.annotationIndex && !row.mine);
    if (next >= 0) { F.annotationIndex = next; showAnnotation(); }
    toast(result.consensus?.agreement === "conflict" ? "Giudizio registrato · il caso è in conflitto" : "Giudizio registrato");
  } catch (error) { toast(error.message, true); }
}

async function withdrawAnnotation() {
  if (!F.annotations[F.annotationIndex]?.mine) return;
  const annotator = await ensureAnnotator(); if (!annotator) return;
  const item = F.annotations[F.annotationIndex]; if (!item || !item.mine) return;
  try {
    const result = await api("/api/annotations/withdraw", {method:"POST",
      body:{image_id:item.image_id, question_id:F.annotationQuestionId, annotator}});
    item.labels = (item.labels || []).filter(label => label.is_adjudication || !isMine(label));
    item.mine = null;
    applyConsensus(item, result.consensus);
    markAgreementStale(); showAnnotation(); toast("Giudizio ritirato");
  } catch (error) { toast(error.message, true); }
}

async function adjudicate(imageId, questionId, value) {
  const annotator = annotatorName();
  if (!annotator) { toast("Scrivi il tuo nome nel campo “Revisore” prima di arbitrare", true); return; }
  if (annotator.toLowerCase() !== (F.annotator || "").toLowerCase()) await setAnnotator(annotator);
  try {
    await api("/api/annotations", {method:"POST", body:{image_id:imageId, question_id:questionId,
      value, annotator, is_adjudication:true, note:"arbitrato"}});
    toast("Caso arbitrato");
    if (questionId === F.annotationQuestionId) await loadAnnotations(questionId); else await loadAgreement();
  } catch (error) { toast(error.message, true); }
}

function markAgreementStale() {
  F.agreementStale = true;
  $("#agreement-badge").textContent = "DA RICALCOLARE";
  $("#agreement-badge").classList.add("stale");
}

async function loadAgreement() {
  if (!F.project) return;
  try {
    const [report, contested] = await Promise.all([
      api(`/api/projects/${F.project.id}/agreement`),
      api(`/api/projects/${F.project.id}/contested`),
    ]);
    F.agreementStale = false;
    $("#agreement-badge").classList.remove("stale");
    renderAgreement(report); renderContested(contested);
  } catch (error) { toast(error.message, true); }
}

function renderAgreement(report) {
  const overall = report.overall || {};
  const measurable = report.annotator_count >= 2 && overall.reliability_units > 0;
  $("#agreement-badge").textContent = measurable ? `α ${coef(overall.krippendorff_alpha)}` : "NON CALCOLABILE";
  $("#agreement-empty").classList.toggle("hidden", measurable);
  $("#agreement-results").classList.toggle("hidden", !measurable);
  $("#agreement-warnings").innerHTML = (report.warnings || []).map(item =>
    `<div class="audit-warning ${esc(item.severity)}"><i>${esc(item.severity)}</i><span><b>${esc(item.area)}</b> — ${esc(item.text)}</span></div>`).join("");
  if (!measurable) {
    $("#agreement-empty").innerHTML = (report.warnings || []).length
      ? `<div class="audit-warnings">${$("#agreement-warnings").innerHTML}</div>`
      : "L’accordo si calcola sui casi giudicati da più di un revisore.";
    $("#agreement-empty").insertAdjacentHTML("beforeend",
      `<div class="consensus-bar">${consensusBarHtml(report.consensus)}</div>`);
    return;
  }
  $("#agreement-reviewers").textContent = report.annotator_count;
  $("#agreement-reviewers-note").textContent = `${report.annotated_cases} casi annotati in tutto`;
  $("#agreement-alpha").textContent = coef(overall.krippendorff_alpha);
  $("#agreement-alpha-note").textContent = `${esc(overall.alpha_label || "")} · 95% ${coef(overall.alpha_ci_low)}–${coef(overall.alpha_ci_high)}`
    + (overall.fleiss_kappa === null || overall.fleiss_kappa === undefined ? "" : ` · Fleiss κ ${coef(overall.fleiss_kappa)}`);
  $("#agreement-observed").textContent = pctOrDash(overall.percent_agreement);
  $("#agreement-observed-note").textContent = "coppie di etichette concordi sullo stesso caso";
  $("#agreement-units").textContent = overall.reliability_units;
  $("#agreement-units-note").textContent = Object.entries(overall.panel_sizes || {})
    .map(([size, count]) => `${count}× panel da ${size}`).join(" · ");
  $("#agreement-consensus").innerHTML = consensusBarHtml(report.consensus);
  $("#agreement-annotators").innerHTML = `<table><thead><tr><th>Revisore</th><th>Etichette</th><th>Sì/No/Inc./Escl.</th><th>Quota sì</th><th>Arbitrati</th></tr></thead><tbody>${
    report.annotators.map(item => `<tr><td><b>${esc(item.annotator)}</b><small class="table-sub">${item.images} immagini · ${item.questions} domande</small></td><td>${item.labels}</td><td class="mono-cell">${item.distribution.yes}/${item.distribution.no}/${item.distribution.uncertain}/${item.distribution.exclude}</td><td>${pctOrDash(item.positive_share)}</td><td>${item.adjudications}</td></tr>`).join("")}</tbody></table>`;
  $("#agreement-pairs").innerHTML = report.pairs.length
    ? `<table><thead><tr><th>Coppia</th><th>Casi in comune</th><th>Accordo</th><th>Cohen κ</th><th>95%</th></tr></thead><tbody>${
      report.pairs.map(pair => `<tr><td><b>${esc(pair.a)} ↔ ${esc(pair.b)}</b>${pair.confusions.length?`<small class="table-sub">${pair.confusions.slice(0,3).map(item => `${VERDICT_LABELS[item.a_value]||item.a_value}→${VERDICT_LABELS[item.b_value]||item.b_value} ×${item.count}`).join(" · ")}</small>`:""}</td><td>${pair.shared}</td><td>${pctOrDash(pair.percent_agreement)}</td><td class="${pair.cohen_kappa !== null && pair.cohen_kappa < .4 ? "delta-negative" : ""}">${coef(pair.cohen_kappa)}<small class="table-sub">${esc(pair.kappa_label)}</small></td><td>${coef(pair.kappa_ci_low)}–${coef(pair.kappa_ci_high)}</td></tr>`).join("")}</tbody></table>`
    : '<div class="empty-state">Nessuna coppia di revisori con casi in comune.</div>';
  $("#agreement-questions").innerHTML = `<table><thead><tr><th>Domanda</th><th>Casi in doppio</th><th>Revisori</th><th>Accordo</th><th>Krippendorff α</th><th>Lettura</th></tr></thead><tbody>${
    report.questions.map(item => `<tr><td><b>${esc(item.label)}</b><small class="table-sub">${esc(item.key)}</small></td><td>${item.reliability_units}</td><td>${item.annotators}</td><td>${pctOrDash(item.percent_agreement)}</td><td class="${item.krippendorff_alpha !== null && item.krippendorff_alpha < .4 ? "delta-negative" : ""}">${coef(item.krippendorff_alpha)}</td><td>${esc(item.alpha_label)}</td></tr>`).join("")}</tbody></table>`;
  $("#agreement-method").textContent = report.method;
  $("#agreement-limitations").textContent = report.limitations;
}

function consensusBarHtml(consensus) {
  if (!consensus || !consensus.cases) return "";
  const order = ["unanimous","majority","adjudicated","single","conflict"];
  return `<div class="consensus-track">${order.map(state =>
      consensus[state] ? `<i class="${state}" style="flex:${consensus[state]}" title="${AGREEMENT_LABELS[state]}: ${consensus[state]}"></i>` : "").join("")}</div>`
    + `<div class="consensus-legend">${order.map(state =>
      `<span><i class="${state}"></i>${AGREEMENT_LABELS[state]} ${consensus[state]}</span>`).join("")}</div>`;
}

function renderContested(contested) {
  $("#conflict-badge").textContent = `${contested.unresolved} APERTI`;
  $("#conflict-badge").classList.toggle("stale", contested.unresolved > 0);
  const cases = contested.cases || [];
  $("#conflict-list").className = cases.length ? "conflict-list" : "conflict-list empty-state";
  $("#conflict-list").innerHTML = cases.length ? cases.slice(0, 40).map(item => `
    <article class="conflict-card ${esc(item.agreement)}">
      <img loading="lazy" src="/media/${item.image_id}" alt="">
      <div>
        <header><b>${esc(item.question_label)}</b><i class="chip ${esc(item.agreement)}">${AGREEMENT_LABELS[item.agreement] || item.agreement}</i></header>
        <code>${esc(item.filename)}</code>
        <div class="conflict-labels">${item.labels.map(label => `<i class="chip ${label.is_adjudication?"adjudicated":"peer"} ${esc(label.value)}" title="${esc(label.note || "")}">${label.is_adjudication?"⚖ ":""}${esc(label.annotator)}: ${VERDICT_LABELS[label.value] || label.value}</i>`).join("")}</div>
        <div class="conflict-actions"><span>Arbitra:</span>${["yes","no","uncertain","exclude"].map(value =>
          `<button class="mini" type="button" data-adjudicate="${item.image_id}:${item.question_id}:${value}">${VERDICT_LABELS[value]}</button>`).join("")}</div>
      </div>
    </article>`).join("") : "Nessun caso conteso.";
}

async function createProvider(event) {
  event.preventDefault(); const form = event.currentTarget;
  try { await api("/api/providers", {method:"POST", body:formObject(form)}); form.reset(); form.elements.endpoint.value = "http://127.0.0.1:11434"; clearDetectedModels(); await refreshState(); toast("Provider locale registrato"); }
  catch (error) { toast(error.message, true); }
}

function selectDetectedModel(event) {
  if (!event.target.value) return;
  const form = $("#provider-form");
  form.elements.model.value = event.target.value;
  $("#model-picker-hint").textContent = "Modello selezionato dall’endpoint privato.";
}

function clearDetectedModels() {
  const select = $("#detected-models");
  select.classList.add("hidden");
  select.innerHTML = '<option value="">Scegli un modello rilevato…</option>';
  $("#model-picker-hint").textContent = "Inserisci il nome oppure usa “Rileva modelli”.";
}

async function discoverProviderModels(event) {
  const button = event.currentTarget, form = $("#provider-form"), original = button.textContent;
  button.disabled = true; button.textContent = "Ricerca…";
  try {
    const result = await api("/api/providers/discover", {method:"POST", body:{kind:form.elements.kind.value,endpoint:form.elements.endpoint.value}});
    const select = $("#detected-models");
    select.innerHTML = '<option value="">Scegli un modello rilevato…</option>' + result.models.map(model => `<option value="${esc(model)}">${esc(model)}</option>`).join("");
    select.classList.toggle("hidden", !result.models.length);
    if (result.models.length) {
      const current = form.elements.model.value;
      const selected = result.models.includes(current) ? current : result.models[0];
      select.value = selected;
      form.elements.model.value = selected;
      $("#model-picker-hint").textContent = result.models.length === 1 ? "Un modello rilevato e selezionato." : `Scegli tra i ${result.models.length} modelli rilevati.`;
    } else {
      $("#model-picker-hint").textContent = "Nessun modello rilevato: puoi inserirlo manualmente.";
    }
    toast(result.models.length ? `${result.models.length} modelli rilevati` : "Nessun modello esposto dal provider", !result.models.length);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

async function createDemoProvider() {
  if (F.state.providers.some(provider => provider.is_demo)) return toast("Il simulatore è già disponibile");
  try {
    await api("/api/providers", {method:"POST", body:{name:"Synthetic Prompt Stressor",kind:"simulator",model:"demo",endpoint:"http://127.0.0.1"}});
    await refreshState(); toast("Simulatore offline pronto");
  } catch (error) { toast(error.message, true); }
}

async function testProvider(id, button) {
  if (!F.project?.images?.length) return toast("Importa almeno un’immagine per testare il modello", true);
  const original = button.textContent; button.disabled = true; button.textContent = "Test in corso…";
  try {
    const result = await api(`/api/providers/${id}/test`, {method:"POST", body:{project_id:F.project.id}});
    toast(`Connessione OK · ${result.answer} · ${result.latency_ms} ms`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

async function deleteProvider(id) {
  const provider = F.state.providers.find(item => item.id === id);
  if (!provider || !window.confirm(`Eliminare “${provider.name}”? Non è recuperabile.`)) return;
  try {
    await api(`/api/providers/${id}`, {method:"DELETE"});
    await refreshState();
    toast("Provider eliminato");
  } catch (error) { toast(error.message, true); }
}

function renderRunQuestions() {
  const questions = F.project?.questions || [];
  $("#run-questions").innerHTML = questions.length ? questions.map(question => `<label class="run-check"><input type="checkbox" name="question_ids" value="${question.id}" checked><span>${esc(question.label)} · ${question.variants.length} varianti</span></label>`).join("") : "Crea prima una domanda.";
}

async function createRun(event) {
  event.preventDefault(); if (!F.project) return toast("Seleziona un progetto", true);
  const raw = formObject(event.currentTarget), question_ids = $$('input[name="question_ids"]:checked', event.currentTarget).map(input => Number(input.value));
  const body = {...raw, project_id:F.project.id, provider_id:Number(raw.provider_id), question_ids, repetitions:Number(raw.repetitions), temperature:Number(raw.temperature), seed:Number(raw.seed)};
  try { const run = await api("/api/runs", {method:"POST", body}); await refreshRuns(); toast("Esecuzione avviata"); $("#run-list").scrollIntoView({behavior:"smooth"}); if (run) pollRuns(); }
  catch (error) { toast(error.message, true); }
}

function fmtEta(seconds) {
  if (seconds === null || seconds === undefined) return "tempo non stimabile";
  if (seconds < 1) return "quasi finito";
  const total = Math.round(seconds), h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  if (h) return `~${h}h ${String(m).padStart(2, "0")}m rimanenti`;
  if (m) return `~${m} min rimanenti`;
  return `~${s}s rimanenti`;
}

function populateRunFilterOptions() {
  const projectSelect = $("#run-filter-project"), providerSelect = $("#run-filter-provider");
  const keepProject = projectSelect.value, keepProvider = providerSelect.value;
  projectSelect.innerHTML = '<option value="">Tutti i progetti</option>'
    + F.state.projects.map(project => `<option value="${project.id}">${esc(project.name)}</option>`).join("");
  providerSelect.innerHTML = '<option value="">Tutti i modelli</option>'
    + F.state.providers.map(provider => `<option value="${provider.id}">${esc(provider.name)}</option>`).join("");
  projectSelect.value = keepProject; providerSelect.value = keepProvider;
}

function matchesRunFilters(run) {
  const project = $("#run-filter-project").value, status = $("#run-filter-status").value,
        provider = $("#run-filter-provider").value, search = $("#run-filter-search").value.trim().toLowerCase();
  if (project && String(run.project_id) !== project) return false;
  if (status && run.status !== status) return false;
  if (provider && String(run.provider_id) !== provider) return false;
  if (search && !run.name.toLowerCase().includes(search)) return false;
  return true;
}

function updateRunExportLink() {
  const params = new URLSearchParams();
  const project = $("#run-filter-project").value; if (project) params.set("project_id", project);
  const status = $("#run-filter-status").value; if (status) params.set("status", status);
  const provider = $("#run-filter-provider").value; if (provider) params.set("provider_id", provider);
  const search = $("#run-filter-search").value.trim(); if (search) params.set("q", search);
  if ($("#run-filter-archived").checked) params.set("archived", "1");
  const query = params.toString();
  $("#run-export-link").href = query ? `/api/runs/export?${query}` : "/api/runs/export";
}

async function loadRuns() {
  if ($("#run-filter-archived").checked) {
    try { F.archivedRuns = (await api("/api/runs?archived=1")).runs; }
    catch (error) { toast(error.message, true); }
  }
  renderRuns();
}

function runCardHtml(run, archived) {
  const progress = run.total ? 100 * run.completed / run.total : 0;
  const active = ["running", "queued"].includes(run.status);
  const resumable = !archived && (["paused", "failed", "cancelled"].includes(run.status) || (run.status === "completed" && run.error));
  const buttons = active
    ? [`<button class="mini" data-pause-run="${run.id}">Pausa</button>`,
       `<button class="mini" data-cancel-run="${run.id}">Ferma</button>`]
    : [run.status === "completed" && `<button class="mini" data-result-run="${run.id}">Apri Atlas</button>`,
       resumable && `<button class="mini resume" data-resume-run="${run.id}">Riprendi</button>`,
       `<button class="mini" data-rename-run="${run.id}">Rinomina</button>`,
       `<button class="mini" data-duplicate-run="${run.id}">Duplica</button>`,
       archived ? `<button class="mini" data-unarchive-run="${run.id}">Ripristina</button>`
                : `<button class="mini" data-archive-run="${run.id}">Archivia</button>`,
       `<button class="mini danger" data-delete-run="${run.id}">Elimina</button>`];
  const eta = active && run.status === "running" ? ` · ${fmtEta(run.eta_seconds)}` : "";
  const demo = run.provider_is_demo ? '<span class="demo-chip">DEMO</span>' : "";
  const archivedChip = archived ? '<span class="archived-chip">ARCHIVIATA</span>' : "";
  return `<div class="run-card"><header><div><b>${esc(run.name)} ${demo}${archivedChip}</b><code>${esc(run.project_name)} · ${esc(run.provider_model)} · ${fmtTime(run.created_at)}</code></div><span class="status ${esc(run.status)}">${esc(run.status)}</span></header><div class="run-progress"><i style="width:${progress}%"></i></div><footer><span>${run.completed}/${run.total || "?"}${eta}</span><div class="run-actions">${buttons.filter(Boolean).join("")}</div></footer>${run.error?`<code>${esc(run.error)}</code>`:""}</div>`;
}

function renderRuns() {
  populateRunFilterOptions(); updateRunExportLink();
  const archived = $("#run-filter-archived").checked;
  const runs = (archived ? F.archivedRuns : F.state.runs).filter(matchesRunFilters);
  $("#run-count").textContent = runs.length;
  $("#run-list").className = runs.length ? "run-list" : "run-list empty-state";
  $("#run-list").innerHTML = runs.length ? runs.map(run => runCardHtml(run, archived)).join("")
    : (archived ? "Nessuna esecuzione archiviata corrisponde ai filtri." : "Nessuna esecuzione corrisponde ai filtri.");
  const completed = F.state.runs.filter(run => run.status === "completed");
  $("#result-run").innerHTML = '<option value="">Seleziona un run completato</option>'
    + completed.map(run => `<option value="${run.id}">${esc(run.name)} · ${esc(run.provider_model)}</option>`).join("");
}

function arenaRunKey(run) {
  const config = run.config || {};
  return JSON.stringify([run.project_id, [...(config.question_ids || [])].map(Number).sort((a,b)=>a-b),
    [...(config.variant_ids || [])].map(Number).sort((a,b)=>a-b), Number(config.repetitions || 1),
    Number(config.temperature || 0), Number(config.seed || 0), Number(config.max_tokens || 96), Boolean(run.provider_is_demo)]);
}

function renderArenaControls() {
  const providers = F.state.providers || [], questions = F.project?.questions || [];
  const providerIds = new Set(F.arenaProviderSelection);
  $("#arena-providers").className = providers.length >= 2 ? "check-list arena-check-list" : "check-list empty-state";
  $("#arena-providers").innerHTML = providers.length ? providers.map(provider => `<label class="run-check"><input type="checkbox" value="${provider.id}" ${providerIds.has(provider.id)?"checked":""}><span><b>${esc(provider.name)}</b><small>${esc(provider.model)}${provider.is_demo?' · DEMO':''}</small></span></label>`).join("") : "Configura almeno due modelli.";
  if (!F.arenaQuestionSelection.length && questions.length) F.arenaQuestionSelection = questions.map(question => question.id);
  const questionIds = new Set(F.arenaQuestionSelection);
  $("#arena-questions").className = questions.length ? "check-list arena-check-list" : "check-list empty-state";
  $("#arena-questions").innerHTML = questions.length ? questions.map(question => `<label class="run-check"><input type="checkbox" value="${question.id}" ${questionIds.has(question.id)?"checked":""}><span><b>${esc(question.label)}</b><small>${question.variants.length} varianti</small></span></label>`).join("") : "Crea prima una domanda.";

  const completed = F.state.runs.filter(run => run.status === "completed" && run.project_id === F.project?.id);
  const groups = new Map();
  completed.forEach(run => { const key = arenaRunKey(run); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(run); });
  if (!F.arenaSelectionTouched && !F.arenaSelection.length) {
    const automatic = [...groups.values()].find(group => group.length >= 2);
    if (automatic) F.arenaSelection = automatic.map(run => run.id);
  }
  const available = new Set(completed.map(run => run.id));
  F.arenaSelection = F.arenaSelection.filter(id => available.has(id));
  $("#arena-run-count").textContent = completed.length;
  $("#arena-run-list").className = completed.length ? "arena-run-list" : "arena-run-list empty-state";
  $("#arena-run-list").innerHTML = completed.length ? [...groups.values()].map((group,index) => {
    const config = group[0].config || {};
    return `<section class="arena-run-group"><header><b>Protocollo ${index+1}</b><span>${group.length} run · ${Number(config.repetitions||1)}× · seed ${Number(config.seed||0)}</span></header>${group.map(run => `<label class="arena-run-choice"><input type="checkbox" value="${run.id}" ${F.arenaSelection.includes(run.id)?"checked":""}><span><b>${esc(run.provider_name)}</b><small>${esc(run.name)} · ${fmtTime(run.created_at)}</small></span><code>${run.completed}/${run.total}</code></label>`).join("")}</section>`;
  }).join("") : "Servono almeno due run completati e compatibili.";
  $("#arena-compare").disabled = F.arenaSelection.length < 2;
}

function updateArenaSelection(event) {
  const input = event.target.closest('input[type="checkbox"]'); if (!input) return;
  F.arenaSelectionTouched = true;
  const id = Number(input.value), selectedRun = F.state.runs.find(run => run.id === id);
  if (!selectedRun) return;
  if (input.checked) {
    const key = arenaRunKey(selectedRun);
    F.arenaSelection = F.arenaSelection.filter(runId => {
      const run = F.state.runs.find(item => item.id === runId);
      return run && arenaRunKey(run) === key;
    });
    if (!F.arenaSelection.includes(id)) F.arenaSelection.push(id);
  } else F.arenaSelection = F.arenaSelection.filter(runId => runId !== id);
  renderArenaControls();
}

async function createArenaRuns(event) {
  event.preventDefault();
  if (!F.project) return toast("Seleziona prima un progetto", true);
  const form = event.currentTarget, button = $("#arena-start"), raw = formObject(form);
  const provider_ids = $$('#arena-providers input:checked').map(input => Number(input.value));
  const question_ids = $$('#arena-questions input:checked').map(input => Number(input.value));
  if (provider_ids.length < 2) return toast("Seleziona almeno due modelli", true);
  if (!question_ids.length) return toast("Seleziona almeno una domanda", true);
  button.disabled = true; button.textContent = "Preparazione Arena…";
  try {
    const result = await api("/api/arena/runs", {method:"POST", body:{...raw,project_id:F.project.id,provider_ids,question_ids,repetitions:Number(raw.repetitions),temperature:Number(raw.temperature),seed:Number(raw.seed)}});
    F.arenaPending = result.run_ids; F.arenaSelection = result.run_ids; F.arenaSelectionTouched = true;
    await refreshRuns(); pollRuns(); toast(`${result.run_ids.length} run accodati in sequenza`);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Avvia Arena sequenziale"; }
}

async function compareArena(showToast = true) {
  const ids = F.arenaSelection;
  if (ids.length < 2) return toast("Seleziona almeno due esecuzioni compatibili", true);
  const button = $("#arena-compare"), original = button.textContent;
  button.disabled = true; button.textContent = "Confronto…";
  try {
    F.arena = await api(`/api/arena?run_ids=${ids.join(",")}`);
    renderArenaResults(F.arena);
    if (showToast) toast("Confronto appaiato completato");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

function signedPP(value) { const number = 100 * Number(value || 0); return `${number>0?"+":""}${number.toFixed(1)} pp`; }
function shortModel(item) { return item.provider_name || item.provider_model || `Run ${item.run_id}`; }
function renderArenaResults(arena) {
  $("#arena-empty").classList.add("hidden"); $("#arena-results").classList.remove("hidden");
  const leader = arena.models[0], compatibility = arena.compatibility;
  const tied = arena.leaders.accuracy === null;
  $("#arena-leader-name").textContent = `${arena.is_demo?"DEMO · ":""}${shortModel(leader)} · ${pct(leader.accuracy)}`;
  $("#arena-leader-note").textContent = `${tied?"Nessun primato: i modelli in testa sono a pari merito su queste unità. ":"Primo soltanto nel protocollo selezionato. "}${leader.matched_units} unità che tutti i modelli hanno saputo giudicare. IC 95% ${pct(leader.ci_low)}–${pct(leader.ci_high)}.${arena.is_demo?" Confronto fra simulatori sintetici: non vale come benchmark.":""}`;
  $("#arena-warning").className = compatibility.fully_matched ? "arena-warning" : "arena-warning arena-warning-loud";
  $("#arena-common-units").textContent = compatibility.common_units;
  $("#arena-overlap-rate").textContent = `${pct(compatibility.overlap_rate)} sovrapposizione`;
  $("#arena-warning").textContent = arena.warning;
  $("#arena-ranking").innerHTML = `<table><thead><tr><th>#</th><th>Modello</th><th>Accuracy su unità comuni · IC 95%</th><th>Scene</th><th>Fragilità</th><th>Repeat</th><th>Copertura / formato</th><th>Latenza mediana / p95</th></tr></thead><tbody>${arena.models.map(model => {
    const badges = [arena.leaders.accuracy===model.run_id?'accurato':'',arena.leaders.speed===model.run_id?'veloce':'',arena.leaders.robustness===model.run_id?'robusto':''].filter(Boolean);
    // leaders.* vale null quando il primato è a pari merito: nessuna coccarda.
    return `<tr><td><b class="arena-rank">${model.rank}</b></td><td><b>${esc(shortModel(model))}</b><small class="table-sub">${esc(model.provider_model)}</small>${badges.length?`<span class="leader-tags">${badges.map(tag=>`<i>${tag}</i>`).join("")}</span>`:""}</td><td><b>${pct(model.accuracy)}</b><small class="table-sub">${pct(model.ci_low)}–${pct(model.ci_high)}</small>${model.coverage < .999 ? `<small class="table-sub">sulle sue sole unità: ${pct(model.own_accuracy)} su ${model.own_units}</small>` : ""}</td><td>${pct(model.scene_balanced_accuracy)}</td><td>${Number(model.prompt_fragility_score).toFixed(1)}</td><td>${Number(model.repeat_instability_score).toFixed(1)}</td><td class="${model.coverage < .999 ? "delta-negative" : ""}">${pct(model.coverage)} / ${pct(model.format_rate)}</td><td>${Math.round(model.median_ms)} / ${Math.round(model.p95_ms)} ms</td></tr>`;
  }).join("")}</tbody></table>`;
  $("#arena-pairwise").innerHTML = `<table><thead><tr><th>Confronto</th><th>Δ accuracy</th><th>IC 95%</th><th>Vittorie</th><th>p esatto</th></tr></thead><tbody>${arena.pairwise.map(pair => `<tr><td><b>${esc(pair.run_a_name)}</b><small class="table-sub">vs ${esc(pair.run_b_name)}</small></td><td class="${pair.accuracy_delta>0?'delta-positive':pair.accuracy_delta<0?'delta-negative':''}">${signedPP(pair.accuracy_delta)}</td><td>${signedPP(pair.delta_ci_low)} → ${signedPP(pair.delta_ci_high)}</td><td>${pair.a_only_correct}–${pair.b_only_correct}<small class="table-sub">${pair.paired} coppie</small></td><td>${Number(pair.mcnemar_p).toPrecision(3)}${pair.mcnemar_p<.05?'<span class="sig-dot">significativo</span>':''}</td></tr>`).join("")}</tbody></table>`;
  const pairMap = new Map(); arena.pairwise.forEach(pair => { pairMap.set(`${pair.run_a_id}:${pair.run_b_id}`, pair.accuracy_delta); pairMap.set(`${pair.run_b_id}:${pair.run_a_id}`, -pair.accuracy_delta); });
  $("#arena-matrix").innerHTML = `<table class="matrix"><thead><tr><th>→</th>${arena.models.map(model=>`<th title="${esc(shortModel(model))}">#${model.rank}</th>`).join("")}</tr></thead><tbody>${arena.models.map(row=>`<tr><th>#${row.rank} ${esc(shortModel(row))}</th>${arena.models.map(column=>row.run_id===column.run_id?'<td class="matrix-self">—</td>':`<td class="${Number(pairMap.get(`${row.run_id}:${column.run_id}`))>0?'delta-positive':'delta-negative'}">${signedPP(pairMap.get(`${row.run_id}:${column.run_id}`))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
  $("#arena-method").textContent = `${arena.interval_method}. Ordinamento: ${arena.ranking_basis}.`;
  $("#arena-results").scrollIntoView({behavior:"smooth",block:"start"});
}

async function handleArenaPending() {
  if (!F.arenaPending.length) return;
  const pending = F.arenaPending.map(id => F.state.runs.find(run => run.id === id)).filter(Boolean);
  if (pending.length !== F.arenaPending.length || pending.some(run => ["queued","running"].includes(run.status))) return;
  const completed = pending.filter(run => run.status === "completed").map(run => run.id);
  F.arenaPending = [];
  if (completed.length >= 2) { F.arenaSelection = completed; renderArenaControls(); await compareArena(false); toast("Model Arena completata"); }
  else toast("Arena non completata: controlla gli errori delle esecuzioni", true);
}

const msOrDash = value => value === null || value === undefined ? "—" : `${Math.round(value)} ms`;
const numOrDash = (value, digits = 1) => value === null || value === undefined ? "—" : Number(value).toFixed(digits);

function fmtWhen(seconds) {
  if (!seconds) return "mai";
  const diff = Date.now() / 1000 - seconds;
  if (diff < 90) return "poco fa";
  if (diff < 3600) return `${Math.round(diff / 60)} min fa`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h fa`;
  return new Date(seconds * 1000).toLocaleDateString();
}

async function loadPerformance() {
  try { F.performance = await api("/api/performance"); renderPerformance(F.performance); }
  catch (error) { toast(error.message, true); }
}

function renderPerformance(report) {
  const models = report.models || [];
  $("#performance-empty").classList.toggle("hidden", models.length > 0);
  $("#performance-list").classList.toggle("hidden", models.length === 0);
  if (!models.length) return;
  const note = `<p class="hint performance-note">${esc(report.method)}</p><p class="hint performance-note">${esc(report.limitations)}</p>`;
  $("#performance-list").innerHTML = note + models.map(item => {
    const memoryRow = item.is_demo
      ? '<div><span>MEMORIA</span><b>—</b><small>il simulatore non alloca memoria reale</small></div>'
      : !item.memory_observable
        ? '<div><span>MEMORIA</span><b>—</b><small>non osservabile su questo protocollo</small></div>'
        : item.memory_bytes !== null
          ? `<div><span>MEMORIA</span><b>${esc(item.memory_display)}</b><small>${item.memory_vram_display ? `${esc(item.memory_vram_display)} VRAM · ` : ""}campionata ${fmtWhen(item.memory_sampled_at)}</small></div>`
          : '<div><span>MEMORIA</span><b>—</b><small>non ancora campionata</small></div>';
    const memoryButtons = (!item.is_demo && item.memory_observable)
      ? `<button class="mini" type="button" data-probe-memory="${item.provider_id}">Verifica memoria adesso</button><button class="mini" type="button" data-free-memory="${item.provider_id}">Libera memoria adesso</button>` : "";
    const warnings = (item.warnings || []).length
      ? `<div class="audit-warnings">${item.warnings.map(w => `<div class="audit-warning ${esc(w.severity)}"><i>${esc(w.severity)}</i><span>${esc(w.text)}</span></div>`).join("")}</div>` : "";
    return `<article class="panel performance-card">
      <div class="panel-head"><div><span class="eyebrow">${esc((item.kind || "").toUpperCase())}${item.is_demo ? " · DEMO" : ""}</span><h3>${esc(item.provider_name)}</h3><code class="mono">${esc(item.model)}</code></div><div class="performance-card-actions">${memoryButtons}</div></div>
      <div class="audit-grid performance-grid">
        <div><span>RISPOSTE</span><b>${item.responses_total}</b><small>${item.responses_errored} errori · ${pct(item.error_rate)}</small></div>
        <div><span>LATENZA MEDIANA</span><b>${msOrDash(item.median_ms)}</b><small>p95 ${msOrDash(item.p95_ms)}</small></div>
        <div><span>TOKEN</span><b>${numOrDash(item.avg_completion_tokens, 0)} out</b><small>${numOrDash(item.avg_prompt_tokens, 0)} in · ${item.tokens_per_second !== null ? `${numOrDash(item.tokens_per_second)} tok/s` : "—"}</small></div>
        <div><span>RESA REALE</span><b>${item.responses_per_second !== null ? numOrDash(item.responses_per_second, 2) : "—"}</b><small>risposte/s, tempo di coda ed errori inclusi</small></div>
        ${memoryRow}
        <div><span>ESECUZIONI</span><b>${item.runs_total}</b><small>${item.runs_active} attive · ${item.runs_failed} fallite</small></div>
      </div>
      ${warnings}
      <p class="hint">Ultimo utilizzo: ${fmtWhen(item.last_used_at)}</p>
    </article>`;
  }).join("");
}

async function probeMemoryFor(id, button) {
  const original = button.textContent; button.textContent = "Verifica…";
  try {
    const result = await api(`/api/providers/${id}/memory`, {method:"POST", body:{}});
    if (result.available) { toast(`Memoria: ${result.display}${result.vram_display ? ` (${result.vram_display} VRAM)` : ""}`); await loadPerformance(); }
    else toast(result.reason, true);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

async function freeMemoryFor(id, button) {
  const original = button.textContent; button.textContent = "Libera…";
  try {
    const result = await api(`/api/providers/${id}/unload`, {method:"POST", body:{}});
    if (result.ok) { toast("Memoria liberata"); await loadPerformance(); }
    else toast(result.reason, true);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}

function pollRuns() {
  clearTimeout(F.poll);
  if (!F.state.runs.some(run => ["running","queued"].includes(run.status))) return;
  F.poll = setTimeout(async () => { try { F.state = await api("/api/state"); renderState(); if (F.view === "performance") loadPerformance(); await handleArenaPending(); pollRuns(); } catch { F.poll = setTimeout(pollRuns, 2000); } }, 1000);
}
async function pauseRun(id) {
  try { await api(`/api/runs/${id}/pause`, {method:"POST",body:{}}); await refreshRuns(); toast("In pausa: le risposte già ottenute sono conservate"); }
  catch (error) { toast(error.message, true); }
}
async function resumeRun(id, button) {
  const original = button.textContent; button.disabled = true; button.textContent = "Ripresa…";
  try {
    const result = await api(`/api/runs/${id}/resume`, {method:"POST",body:{}});
    await refreshRuns(); pollRuns();
    toast(result.resumed ? `Ripresa: ${result.resumed} risposte mancanti su ${result.total}, le altre non vengono rifatte` : "Non manca nulla: esecuzione già completa");
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = original; }
}
async function cancelRun(id) {
  const run = F.state.runs.find(item => item.id === id);
  if (run && !window.confirm(`Fermare “${run.name}”? A differenza della pausa, non riprenderà da sola: userai Riprendi per continuare, oppure Elimina.`)) return;
  try { await api(`/api/runs/${id}/cancel`, {method:"POST",body:{}}); await refreshRuns(); toast("Esecuzione fermata"); } catch(error){ toast(error.message,true); }
}
function findRun(id) {
  return F.state.runs.find(item => item.id === id) || F.archivedRuns.find(item => item.id === id);
}

async function refreshRuns() {
  await refreshState();
  if ($("#run-filter-archived").checked) await loadRuns();
}

async function deleteRun(id) {
  const run = findRun(id);
  if (!run || !window.confirm(`Eliminare definitivamente “${run.name}” e tutti i suoi risultati?`)) return;
  try {
    await api(`/api/runs/${id}`, {method:"DELETE"});
    F.archivedRuns = F.archivedRuns.filter(item => item.id !== id);
    await refreshRuns();
    toast("Esecuzione e risultati eliminati");
  } catch (error) { toast(error.message, true); }
}

function openRunRename(id) {
  const run = findRun(id); if (!run) return;
  const form = $("#run-rename-form");
  form.elements.run_id.value = id; form.elements.name.value = run.name;
  $("#run-rename-dialog").showModal();
}

async function submitRunRename(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api(`/api/runs/${form.elements.run_id.value}/rename`, {method:"POST", body:{name: form.elements.name.value}});
    $("#run-rename-dialog").close(); await refreshRuns(); toast("Esecuzione rinominata");
  } catch (error) { toast(error.message, true); }
}

function openRunDuplicate(id) {
  const run = findRun(id); if (!run) return;
  const form = $("#run-duplicate-form");
  form.elements.run_id.value = id; form.elements.name.value = "";
  $("#run-duplicate-dialog").showModal();
}

async function submitRunDuplicate(event) {
  event.preventDefault();
  const form = event.currentTarget;
  try {
    await api(`/api/runs/${form.elements.run_id.value}/duplicate`, {method:"POST", body:{name: form.elements.name.value}});
    $("#run-duplicate-dialog").close(); await refreshRuns(); pollRuns();
    toast("Esecuzione duplicata e avviata");
  } catch (error) { toast(error.message, true); }
}

async function runArchiveAction(id, restore) {
  const run = findRun(id); if (!run) return;
  try {
    await api(`/api/runs/${id}/${restore ? "unarchive" : "archive"}`, {method:"POST", body:{}});
    if (!restore) F.archivedRuns = F.archivedRuns.filter(item => item.id !== id);
    await refreshRuns();
    toast(restore ? "Esecuzione ripristinata dall’archivio" : "Esecuzione archiviata");
  } catch (error) { toast(error.message, true); }
}

function fragilityVerdict(score) {
  if (score < 3) return ["Robusto nelle varianti osservate","Le formulazioni confrontate producono verdetti quasi identici."];
  if (score < 12) return ["Sensibilità contenuta","Alcuni casi cambiano. Controlla se sono immagini realmente ambigue."];
  if (score < 30) return ["Prompt fragile","La formulazione influenza materialmente la conclusione."];
  return ["Claim instabile","La classifica non regge alle mutazioni osservate."];
}

async function loadResults(runId) {
  if (!runId) { $("#results-empty").classList.remove("hidden"); $("#results-content").classList.add("hidden"); return; }
  try {
    F.metrics = await api(`/api/runs/${runId}/metrics`); const m = F.metrics, s = m.summary, score = Number(s.prompt_fragility_score), gate = m.evidence_gate;
    $("#results-empty").classList.add("hidden"); $("#results-content").classList.remove("hidden");
    $("#result-fragility").textContent = score.toFixed(1); $("#score-ring").style.strokeDashoffset = String(314 - 314 * Math.min(100,score)/100);
    const verdict = fragilityVerdict(score), selectedRun = F.state.runs.find(run => run.id === runId); $("#result-verdict").textContent = verdict[0];
    const normalization = s.normalized_inputs ? ` ${s.normalized_inputs}/${s.input_images} immagini ridotte localmente per il modello; originali invariati.` : "";
    const coverageWarning = Number(s.coverage) < .95 ? ` Copertura insufficiente: ${s.parsed}/${s.evaluated} risposte utilizzabili.` : "";
    $("#result-explain").textContent = `${selectedRun?.provider_is_demo?'Simulazione sintetica · ':''}${verdict[1]} ${s.prompt_comparisons} confronti appaiati.${normalization}${coverageWarning}`;
    $("#result-accuracy").textContent = `${pct(s.accuracy)} · ${s.parsed}/${s.evaluated}`; $("#result-scene").textContent = pct(s.scene_balanced_accuracy); $("#result-baseline").textContent = pct(s.majority_baseline); $("#result-format").textContent = pct(s.format_rate); $("#result-repeat").textContent = `${Number(s.repeat_instability_score).toFixed(1)}`; $("#result-gate").textContent = gate.grade; $("#result-strict").textContent = pct(s.strict_share);
    renderParserHonesty(m);
    $("#evidence-grade").textContent = gate.grade; $("#evidence-grade").dataset.grade = gate.grade; $("#evidence-status").textContent = ({strong:"Prova forte",reviewable:"Claim revisionabile",exploratory:"Claim esplorativo",insufficient:"Prova insufficiente"})[gate.status] || gate.status;
    $("#evidence-checks").innerHTML = gate.checks.map(item => `<div class="${item.passed?'pass':'fail'}"><b>${item.passed?'✓':'×'}</b><span>${esc(item.label)}</span><code>${esc(item.display ?? item.value)}</code></div>`).join("");
    $("#overview-score").textContent = score.toFixed(1);
    $("#variant-bars").innerHTML = m.variants.map(item => `<div class="variant-bar"><label title="${esc(item.variant_name)}">${esc(item.question_key)} / ${esc(item.variant_name)}</label><div class="variant-track"><i class="variant-fill" style="width:${100*item.accuracy}%"></i><i class="variant-baseline" style="left:${100*s.majority_baseline}%"></i></div><b title="risposte valide / casi annotati">${pct(item.accuracy)} · ${item.parsed}/${item.samples}</b></div>`).join("") || "Nessuna risposta comparabile.";
    $("#comparison-table").innerHTML = `<table><thead><tr><th>Variante</th><th>Coppie</th><th>Canonica</th><th>Alternativa</th><th>p</th></tr></thead><tbody>${m.comparisons.map(item => `<tr><td>${esc(item.alternative_name)}</td><td>${item.paired}</td><td>${item.canonical_only_correct}</td><td>${item.alternative_only_correct}</td><td>${Number(item.mcnemar_p).toPrecision(3)}</td></tr>`).join("") || '<tr><td colspan="5">Servono almeno due varianti.</td></tr>'}</tbody></table>`;
    $("#fragile-cases").innerHTML = m.fragile_cases.slice(0,12).map(item => `<div class="fragile-case"><img loading="lazy" src="/media/${item.image_id}" alt=""><span>${item.disagreements}/${item.comparisons} cambi · truth ${esc(item.ground_truth||"—")}</span></div>`).join("") || '<div class="empty-state">Nessun cambio di verdetto osservato.</div>';
    $("#report-link").href = `/api/runs/${runId}/report`; $("#markdown-link").href = `/api/runs/${runId}/report.md`; $("#bundle-link").href = `/api/runs/${runId}/bundle`; $("#report-link").classList.remove("disabled"); $("#markdown-link").classList.remove("disabled"); $("#bundle-link").classList.remove("disabled");
    loadDiagnostics(runId);
  } catch (error) { toast(error.message, true); }
}

const parserLabel = name => ({json:"JSON conforme allo schema",word:"parola isolata nel testo",ambiguous:"prosa con più verdetti",truncated:"ragionamento troncato",none:"nessun verdetto leggibile",error:"chiamata fallita"}[name] || name);
function renderParserHonesty(m) {
  const s = m.summary, rows = m.parser_breakdown || [];
  $("#parser-badge").textContent = `${pct(s.strict_share)} JSON`;
  const weak = 1 - Number(s.strict_share || 0);
  $("#parser-note").textContent = `Un verdetto ricavato dalla prosa vale meno di un JSON conforme allo schema: ${pct(s.strict_share)} dei verdetti conteggiati è arrivato in forma stretta.${weak > .1 ? ` Il ${pct(weak)} poggia su una lettura del testo, quindi guarda la riga corrispondente prima di citare l’accuracy complessiva.` : ""}${s.tie_units ? ` ${s.tie_units} unità scartate perché le ripetizioni si sono divise a metà: il modello non ha scelto.` : ""}`;
  $("#parser-table").innerHTML = rows.length ? `<table><thead><tr><th>Parser</th><th>Verdetti</th><th>Quota</th><th>Accuracy</th><th>IC 95%</th></tr></thead><tbody>${rows.map(item => `<tr><td><b>${esc(item.parser)}</b><small class="table-sub">${esc(parserLabel(item.parser))}</small></td><td>${item.parsed}</td><td>${pct(item.share)}</td><td class="${item.parser!=="json"?"delta-negative":""}">${pct(item.accuracy)}</td><td>${pct(item.ci_low)}–${pct(item.ci_high)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty-state">Nessun verdetto leggibile da attribuire.</div>';
}

async function loadDiagnostics(runId) {
  F.diagnosticRunId=runId; $("#diagnosis-summary").textContent="Analisi locale delle immagini in corso…";
  $("#diagnosis-patterns").innerHTML=""; $("#diagnosis-clusters").innerHTML="";
  try {
    const diagnosis=await api(`/api/runs/${runId}/diagnostics`); if (F.diagnosticRunId!==runId) return;
    const summary=diagnosis.summary;
    $("#diagnosis-coverage").textContent=`FEATURES ${pct(summary.feature_coverage)}`;
    $("#diagnosis-summary").innerHTML=`<div><b>${summary.failures}</b><span>errori su ${summary.evaluated_units} unità</span></div><div><b>${pct(summary.failure_rate)}</b><span>tasso d’errore</span></div><div><b>${summary.format_failures}</b><span>problemi di formato</span></div>`;
    $("#diagnosis-patterns").innerHTML=diagnosis.risk_patterns.length?diagnosis.risk_patterns.map(pattern=>`<article class="diagnosis-pattern"><header><b>${esc(pattern.label)}</b><strong>+${(100*pattern.delta).toFixed(1)} pp</strong></header><p>${esc(pattern.explanation)}</p><div class="diagnosis-meter"><i style="width:${100*pattern.failure_rate}%"></i></div><small>${pattern.failures}/${pattern.samples} errori · IC 95% ${pct(pattern.ci_low)}–${pct(pattern.ci_high)}</small><div class="diagnosis-examples">${pattern.example_image_ids.slice(0,4).map(id=>`<img loading="lazy" src="/media/${id}" alt="Esempio del pattern">`).join("")}</div></article>`).join(""):'<div class="empty-state">Nessun sottogruppo mostra un rischio superiore agli altri nei dati disponibili.</div>';
    $("#diagnosis-clusters").innerHTML=diagnosis.clusters.length?diagnosis.clusters.map(cluster=>`<article class="diagnosis-cluster"><div><b>${esc(cluster.label)}</b><small>${cluster.failures} ${cluster.failures === 1 ? "fallimento" : "fallimenti"}</small></div><div>${cluster.image_ids.slice(0,5).map(id=>`<img loading="lazy" src="/media/${id}" alt="Caso nel cluster">`).join("")}</div></article>`).join(""):'<div class="empty-state">Nessun errore da raggruppare.</div>';
    $("#diagnosis-limitations").textContent=diagnosis.limitations;
  } catch(error) { if(F.diagnosticRunId===runId) $("#diagnosis-summary").textContent=`Diagnosi non disponibile: ${error.message}`; }
}

boot().catch(error => { console.error(error); toast(`Avvio non riuscito: ${error.message}`, true); });
