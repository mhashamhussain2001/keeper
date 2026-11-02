"use strict";
const $ = id => document.getElementById(id);
const seconds = value => `${Math.floor(Math.max(0, value) / 60)}:${Math.floor(Math.max(0, value) % 60).toString().padStart(2, "0")}`;
const takeName = take => `${take.section === "unsorted" ? "Ideas" : take.section} · ${take.variant}`;
const state = {takes:[], selected:[], memo:null, data:null, epoch:0, uploading:false};
const sectionOrder = ["intro", "verse", "pre-chorus", "chorus", "bridge", "hook", "outro", "unsorted", "unlabeled", "rejected"];
let owner;
function randomOwner() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
try {
  owner = localStorage.getItem("keeper-owner");
  if (!/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(owner || "")) {
    owner = randomOwner(); localStorage.setItem("keeper-owner", owner);
  }
} catch (_) {
  owner = randomOwner();
  $("privacy-hint").textContent = "Browser storage is unavailable. Memos uploaded here will be accessible only until this page closes.";
}
function status(message, error = false) { $("status").textContent = message; $("status").classList.toggle("error", error); }
async function apiFetch(url, options = {}) {
  const headers = new Headers(options.headers || {}); headers.set("X-Keeper-Owner", owner);
  const response = await fetch(url, {...options, headers, cache:"no-store"});
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `Request failed (${response.status}).`);
  }
  return response;
}
async function request(url, options) { return (await apiFetch(url, options)).json(); }
function element(tag, className = "", text = "") {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}
function svgElement(tag, attributes = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  node.textContent = text; return node;
}
let iosHintShown = false;
const player = new KeeperPlayer({
  fetchClip: async url => (await apiFetch(url)).arrayBuffer(),
  onError: message => status(message, true),
  onStart: () => {
    const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    if (ios && !iosHintShown) { $("ios-hint").hidden = false; iosHintShown = true; }
  },
  onChange: playback => {
    const playing = playback.mode === "playing";
    $("transport-play").textContent = playing ? "Pause" : "Play";
    $("transport-play").disabled = playback.mode === "loading" || (!playback.queue.length && !state.selected.length);
    $("stop").disabled = !playback.queue.length;
    $("playback-time").textContent = `${seconds(playback.position)} / ${seconds(playback.total)}`;
    $("seek").max = String(playback.total || 1); $("seek").value = String(playback.position); $("seek").disabled = !playback.total || playback.mode === "loading";
    const label = playing ? "Playing" : playback.mode === "loading" ? "Loading" : playback.mode === "stopped" ? "Stopped" : playback.mode === "ended" ? "Finished" : "Paused";
    $("playing-status").textContent = playback.current ? `${label}: ${takeName(playback.current)}` : "Nothing playing";
    for (const node of document.querySelectorAll("[data-take]")) {
      const active = playback.current?.id === node.dataset.take && playing;
      node.classList.toggle("playing", active);
      const button = node.querySelector(".play-take");
      if (button) { button.textContent = active ? "Pause" : "Play"; button.setAttribute("aria-label", `${active ? "Pause" : "Play"} ${takeName(state.takes.find(take => take.id === node.dataset.take))}`); }
    }
  }
});
function updateSelection() {
  for (const node of document.querySelectorAll("[data-take]")) {
    const index = state.selected.indexOf(node.dataset.take); node.classList.toggle("selected", index >= 0);
    const checkbox = node.querySelector("input[type=checkbox]"); if (checkbox) checkbox.checked = index >= 0;
    const order = node.querySelector(".order"); if (order) order.textContent = index >= 0 ? String(index + 1) : "";
    if (node.getAttribute("role") === "button") node.setAttribute("aria-pressed", String(index >= 0));
  }
  $("queue-description").textContent = state.selected.length ? state.selected.map(id => takeName(state.takes.find(take => take.id === id))).join(" → ") : "Select at least two takes for back-to-back playback.";
  $("play-selected").disabled = state.selected.length < 2 || state.uploading;
  player.emit();
}
function toggleSelection(take) {
  if (!take.clip_url) return;
  const index = state.selected.indexOf(take.id);
  if (index >= 0) state.selected.splice(index, 1); else state.selected.push(take.id);
  updateSelection();
}
function playTake(take) {
  // toggle() calls AudioContext.resume() and plays the silent sample before returning a promise.
  const queue = player.current?.id === take.id && ["playing", "paused"].includes(player.mode) ? player.queue : [take];
  player.toggle(queue).catch(error => status(error.message, true));
}
function visualRegions(regions) {
  const result = regions.map(region => ({...region}));
  while (result.length > 1) {
    const index = result.findIndex(region => region.end - region.start < 1.5); if (index < 0) break;
    const short = result[index], before = result[index - 1], after = result[index + 1];
    const previous = before && (!after || before.end - before.start >= after.end - after.start);
    if (previous) before.end = short.end; else after.start = short.start;
    result.splice(index, 1);
  }
  return result.reduce((merged, region) => {
    const last = merged.at(-1); if (last?.kind === region.kind) last.end = region.end; else merged.push(region); return merged;
  }, []);
}
function renderTimeline() {
  const data = state.data, svg = $("timeline"); svg.replaceChildren(); if (!data) return;
  const x = time => 12 + (time / Math.max(data.duration_s, 0.001)) * 976;
  const show = $("show-detail").checked; $("activity-legend").hidden = !show;
  const lanes = [], blocks = [];
  for (const take of [...state.takes].sort((a, b) => a.start - b.start)) {
    let lane = lanes.findIndex(end => end <= take.start); if (lane < 0) lane = lanes.length;
    lanes[lane] = take.end; blocks.push({take, lane});
  }
  const height = 100 + Math.max(1, lanes.length) * 32;
  svg.setAttribute("viewBox", `0 0 1000 ${height}`);
  if (show) for (const region of visualRegions(data.regions)) {
    svg.append(svgElement("rect", {x:x(region.start), y:6, width:Math.max(0, x(region.end) - x(region.start)), height:18,
      fill:`var(--${region.kind})`}, ""));
  }
  const peaks = data.peaks || [];
  const points = peaks.map((peak, index) => {
    const px = 12 + index / Math.max(1, peaks.length - 1) * 976, amplitude = Math.max(0.6, peak * 26);
    return `M ${px} ${57 - amplitude} V ${57 + amplitude}`;
  }).join(" ");
  svg.append(svgElement("path", {d:points, stroke:"var(--muted)", "stroke-opacity":0.35, "stroke-width":1, fill:"none"}));
  for (const {take, lane} of blocks) {
    const left = x(take.start), width = Math.max(4, x(take.end) - left), y = 94 + lane * 32;
    const group = svgElement("g", {class:"timeline-take", "data-take":take.id, tabindex:0, role:"button", "aria-label":`Select ${takeName(take)}`});
    group.append(svgElement("rect", {x:left, y, width, height:25, rx:3, fill:"var(--soft)", stroke:"currentColor"}));
    group.append(svgElement("text", {x:left + 3, y:y + 17}, takeName(take)));
    group.addEventListener("click", () => toggleSelection(take));
    group.addEventListener("keydown", event => { if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); toggleSelection(take); } });
    svg.append(group);
  }
  updateSelection();
}
function renderSongMap() {
  const map = $("song-map"); map.replaceChildren();
  const sections = state.data.songmap.sections;
  for (const section of [...sectionOrder, ...Object.keys(sections).filter(name => !sectionOrder.includes(name))]) {
    const takes = sections[section]; if (!takes?.length) continue;
    const column = element(section === "rejected" ? "details" : "section", "section-column");
    const heading = element(section === "rejected" ? "summary" : "h3", "section-title", section === "unsorted" ? "Ideas" : section);
    heading.append(element("span", "section-count", String(takes.length))); column.append(heading);
    for (const take of state.takes.filter(take => take.section === section)) {
      const card = element("article", "take-card"); card.dataset.take = take.id;
      const label = element("label", "take-label"), checkbox = element("input"); checkbox.type = "checkbox"; checkbox.disabled = !take.clip_url;
      checkbox.setAttribute("aria-label", `Select ${takeName(take)}`); checkbox.addEventListener("change", () => toggleSelection(take));
      label.append(checkbox, element("span", "", take.variant), element("span", "order"));
      const footer = element("div", "take-footer"), play = element("button", "play-take", "Play");
      play.disabled = !take.clip_url; play.addEventListener("click", () => playTake(take));
      footer.append(element("span", "take-duration", seconds(take.end - take.start)), play);
      card.append(label, element("p", "take-times", `${seconds(take.start)} — ${seconds(take.end)}`), footer);
      if (take.heard) card.append(element("p", "take-heard", take.heard)); column.append(card);
    }
    map.append(column);
  }
  if (!state.takes.length) map.append(element("p", "empty", "No takes found in this memo."));
}
function renderTiming(progress) {
  const history = progress.history || [], breakdown = $("stage-breakdown"); breakdown.replaceChildren();
  if (history.length < 2 || progress.state !== "done") { $("processed-time").textContent = "Processing time unavailable"; return; }
  const elapsed = Math.max(0, (Date.parse(history.at(-1).updated_at) - Date.parse(history[0].updated_at)) / 1000);
  $("processed-time").textContent = `Processed in ${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s`;
  history.slice(0, -1).forEach((row, index) => {
    const duration = (Date.parse(history[index + 1].updated_at) - Date.parse(row.updated_at)) / 1000;
    breakdown.append(element("dt", "", row.stage), element("dd", "", `${duration.toFixed(1)} s`));
  });
}
async function loadMemo(id) {
  const epoch = ++state.epoch; player.clear(); state.selected = []; state.memo = id; state.takes = []; state.data = null;
  $("song-map").replaceChildren(element("p", "empty", "Loading memo…")); $("timeline").replaceChildren();
  $("memo-actions").hidden = true; $("delete-confirm").hidden = true; $("rename-form").hidden = true; $("show-detail").checked = false;
  try {
    const base = `/api/memos/${encodeURIComponent(id)}`;
    const [data, progress] = await Promise.all([request(base), request(base + "/status")]);
    if (epoch !== state.epoch) return;
    state.data = data;
    state.takes = Object.entries(data.songmap.sections).flatMap(([section, takes]) => takes.map(take => ({...take, section})));
    $("memo-title").textContent = data.meta.title; $("memo-actions").hidden = !data.meta.owned;
    $("memo-summary").textContent = `${seconds(data.duration_s)} · ${state.takes.length} takes · Parser: ${data.songmap.parser || "legacy / unknown"}`;
    renderTiming(progress); renderSongMap(); renderTimeline(); updateSelection();
    status("Select takes in the order you want to hear them.");
  } catch (error) { if (epoch === state.epoch) status(error.message, true); }
}
async function refreshMemos(preferred) {
  const memos = await request("/api/memos"), picker = $("memo-picker"); picker.replaceChildren();
  for (const memo of memos) picker.append(new Option(memo.title, memo.id));
  if (!memos.length) {
    ++state.epoch; player.clear(); state.memo = null; state.data = null; state.takes = []; state.selected = [];
    picker.append(new Option("No memos yet", "")); $("memo-title").textContent = "Your memos"; $("memo-summary").textContent = "";
    $("memo-actions").hidden = true; $("rename-form").hidden = true; $("delete-confirm").hidden = true;
    $("processed-time").textContent = ""; $("stage-breakdown").replaceChildren(); $("timeline").replaceChildren();
    $("song-map").replaceChildren(element("p", "empty", "Upload a recording to start your first song map."));
    status("Your memos are private to this browser. Upload a recording to get started."); updateSelection(); return;
  }
  const firstOwned = memos.filter(memo => memo.owned).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const id = memos.find(memo => memo.id === preferred)?.id || firstOwned?.id || memos.find(memo => memo.showcase).id;
  picker.value = id; await loadMemo(id);
}
async function waitForMemo(id) {
  while (true) {
    const progress = await request(`/api/memos/${encodeURIComponent(id)}/status`); $("processing-stage").textContent = progress.stage;
    if (progress.state === "done") return;
    if (progress.state === "failed") throw new Error(progress.error || "Memo processing failed.");
    await new Promise(resolve => setTimeout(resolve, 3000));
  }
}
$("memo-picker").addEventListener("change", event => { if (event.target.value) loadMemo(event.target.value); });
$("show-detail").addEventListener("change", renderTimeline);
$("upload-button").addEventListener("click", () => $("upload-file").click());
$("stop").addEventListener("click", () => player.stop());
$("transport-play").addEventListener("click", () => player.toggle(player.queue.length ? player.queue : state.selected.map(id => state.takes.find(take => take.id === id))).catch(error => status(error.message, true)));
$("play-selected").addEventListener("click", () => player.toggle(state.selected.map(id => state.takes.find(take => take.id === id))).catch(error => status(error.message, true)));
$("seek").addEventListener("input", event => player.seek(Number(event.target.value)).catch(error => status(error.message, true)));
$("rename").addEventListener("click", () => { $("rename-input").value = state.data.meta.title; $("rename-form").hidden = false; $("rename-input").focus(); });
$("rename-cancel").addEventListener("click", () => { $("rename-form").hidden = true; });
$("rename-form").addEventListener("submit", async event => {
  event.preventDefault(); const id = state.memo;
  try { await request(`/api/memos/${encodeURIComponent(id)}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({title:$("rename-input").value})}); await refreshMemos(id); }
  catch (error) { status(error.message, true); }
});
$("delete").addEventListener("click", () => { $("delete-confirm").hidden = false; });
$("delete-cancel").addEventListener("click", () => { $("delete-confirm").hidden = true; });
$("delete-yes").addEventListener("click", async () => {
  player.clear(); const id = state.memo; $("delete-yes").disabled = true;
  try { await request(`/api/memos/${encodeURIComponent(id)}`, {method:"DELETE"}); await refreshMemos(); }
  catch (error) { status(error.message, true); }
  finally { $("delete-yes").disabled = false; }
});
$("upload-file").addEventListener("change", async event => {
  const file = event.target.files[0]; if (!file) return;
  if (file.size > 50 * 1024 * 1024) { status("Audio upload must be at most 50 MB.", true); event.target.value = ""; return; }
  player.clear(); state.uploading = true; $("upload-button").disabled = true; $("memo-picker").disabled = true;
  $("processing-panel").hidden = false; $("processing-name").textContent = "Processing your memo"; $("processing-stage").textContent = "Uploading and validating audio";
  const started = performance.now(), timer = setInterval(() => { $("processing-elapsed").textContent = seconds((performance.now() - started) / 1000); }, 1000);
  updateSelection();
  try {
    const now = new Date(), title = `Memo, ${now.toLocaleDateString("en-US", {month:"short", day:"numeric"})}, ${now.toLocaleTimeString("en-US", {hour:"numeric", minute:"2-digit", hour12:true})}`;
    const form = new FormData(); form.append("file", file); form.append("title", title);
    const result = await request("/api/memos", {method:"POST", body:form}); await waitForMemo(result.memo_id);
    await refreshMemos(result.memo_id); $("processing-stage").textContent = "Done";
  } catch (error) { $("processing-stage").textContent = error.message; status(error.message, true); }
  finally { clearInterval(timer); $("processing-elapsed").textContent = seconds((performance.now() - started) / 1000); state.uploading = false;
    $("upload-button").disabled = false; $("memo-picker").disabled = false; event.target.value = ""; updateSelection(); }
});
window.addEventListener("pagehide", () => player.stop());
refreshMemos().catch(error => status(error.message, true));
