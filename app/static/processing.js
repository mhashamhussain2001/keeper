"use strict";
/* Only stored pipeline events drive this view. Timers pace reading and animation. */
(() => {
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ease = () => getComputedStyle(document.documentElement).getPropertyValue("--ease-out").trim() || "cubic-bezier(0.23, 1, 0.32, 1)";
  const card = el("section", "card listening-log"); card.id = "listening-log"; card.hidden = true;
  const heading = el("div", "card-heading"), label = el("label", "listening-label"), toggle = el("input");
  toggle.type = "checkbox"; toggle.id = "process-only";
  try { toggle.checked = localStorage.getItem("keeper-process-only") === "true"; } catch (_) {}
  label.append(toggle, document.createTextNode("Show process only")); heading.append(el("h2", "", "Listening, one idea at a time"), label);
  const entries = el("div", "listening-entries"); entries.id = "listening-entries"; entries.role = "log";
  entries.setAttribute("aria-live", "polite"); entries.setAttribute("aria-relevant", "additions");
  card.append(heading, entries); document.querySelector(".recording").after(card);
  toggle.onchange = () => { card.classList.toggle("process-only", toggle.checked); try { localStorage.setItem("keeper-process-only", String(toggle.checked)); } catch (_) {} };
  toggle.onchange();
  let active = null;

  class Listening {
    constructor(id, peaks = [], replay = false) {
      active?.cancel(); active = this;
      this.id = id; this.peaks = peaks; this.replay = replay; this.events = []; this.queue = [];
      this.animations = new Set(); this.clones = new Set(); this.phase = "listening"; this.duration = 1;
      this.skipped = false; this.seen = 0; this.timer = null; this.deadline = null; this.loaded = false;
      this.drained = new Promise(resolve => { this.resolveDrain = resolve; });
      entries.replaceChildren(); card.hidden = false; document.body.classList.add("listening");
      document.querySelector(".recording h2").textContent = "Listening";
      this.input = event => {
        if (!(this.replay || this.phase === "chopping")) return;
        if (event.type === "keydown" && !["Escape", "Space"].includes(event.code)) return;
        if (event.type === "click" && event.target.closest(".listening-label")) return;
        event.preventDefault(); event.stopImmediatePropagation(); this.skip();
      };
      document.addEventListener("click", this.input, true); document.addEventListener("keydown", this.input, true);
      this.draw();
    }
    animate(node, frames, options) {
      if (this.skipped || reduced()) return Promise.resolve();
      const animation = node.animate(frames, {easing: ease(), fill: "both", ...options}); this.animations.add(animation);
      return animation.finished.catch(() => {}).finally(() => { animation.cancel(); this.animations.delete(animation); });
    }
    feed(status) {
      if (this.phase !== "listening") return;
      if (!this.peaks.length && status.peaks?.length) { this.peaks = status.peaks; if (this.loaded) this.draw(); }
      const incoming = status.events || [];
      for (let i = this.seen; i < incoming.length; i++) {
        const event = incoming[i]; this.events.push(event); this.queue.push({event, index: i});
        // Waveform observations are immediate; readable log lines have their own queue.
        this.observe(event);
        if (["done", "failed"].includes(event.type)) this.deadline = performance.now() + 1800;
      }
      this.seen = incoming.length;
      if (["done", "failed"].includes(status.state) && !this.deadline) this.deadline = performance.now() + 1800;
      if (this.deadline && this.timer !== null) { clearTimeout(this.timer); this.timer = null; }
      if (this.skipped || reduced()) this.flush(); else this.pump();
    }
    observe(event) {
      const d = event.data;
      if (event.type === "received") this.duration = d.duration_s || 1;
      if (event.type === "loading_done") { this.loaded = true; this.duration = d.duration_s || 1; this.draw(true); }
      if (event.type === "ast_start") this.windows = d.n_windows;
      if (event.type === "ast_progress") {
        this.doneWindows = d.windows_done;
        const scanner = $("listening-scanner");
        if (scanner) {
          const target = Math.min(1, d.windows_done / Math.max(1, this.windows)) * recordWidth();
          const previous = this.scannerX || 0; this.scannerX = target;
          scanner.style.transform = `translateX(${target}px)`;
          this.animate(scanner, [{transform: `translateX(${previous}px)`}, {transform: `translateX(${target}px)`}], {duration: 350});
        }
      }
      if (event.type === "regions_found") { this.speech = d.speech_regions || []; this.draw(); }
      if (event.type === "spark_result") { this.pins = [...(this.pins || []), d]; this.draw(); const pin = $("timeline").querySelector(".listening-pin:last-of-type"); if (pin) this.animate(pin, [{opacity: 0, transform: "translateY(4px)"}, {opacity: 1, transform: "translateY(0)"}], {duration: 180}); }
    }
    draw(entrance = false) {
      if (this.phase !== "listening") return;
      const node = $("timeline"), width = Math.max(1, $("record-scroll").clientWidth);
      node.style.width = `${width}px`; node.style.height = "112px"; node.setAttribute("viewBox", `0 0 ${width} 112`); node.replaceChildren();
      for (const region of this.speech || []) node.append(svg("rect", {x: region.start / this.duration * width, y: 16, width: (region.end - region.start) / this.duration * width, height: 74, fill: "var(--text-muted)", opacity: .2}));
      if (!this.loaded || !this.peaks.length) node.append(svg("line", {x1: 0, x2: width, y1: 53, y2: 53, stroke: "var(--text-muted)", "stroke-width": 2, class: "listening-breath"}));
      else for (let group = 0; group < 20; group++) {
        const bars = svg("g");
        for (let i = Math.floor(group * this.peaks.length / 20); i < Math.floor((group + 1) * this.peaks.length / 20); i++) {
          const x = (i + .5) / this.peaks.length * width, h = Math.max(2, this.peaks[i] * 64);
          bars.append(svg("line", {x1: x, x2: x, y1: 53 - h / 2, y2: 53 + h / 2, stroke: "var(--wave-active)", "stroke-width": Math.max(1, width / this.peaks.length * .55)}));
        }
        node.append(bars); if (entrance) this.animate(bars, [{opacity: 0}, {opacity: 1}], {duration: 220, delay: group * 20});
      }
      for (const pin of this.pins || []) { const x = pin.start / this.duration * width, group = svg("g", {class: "listening-pin"}); group.append(svg("title", {}, pin.heard), svg("line", {x1: x, x2: x, y1: 78, y2: 96, stroke: "var(--accent)"}), svg("circle", {cx: x, cy: 78, r: 4, fill: "var(--accent)"})); node.append(group); }
      const scanner = svg("line", {id: "listening-scanner", x1: 0, x2: 0, y1: 10, y2: 96, stroke: "var(--accent)", "stroke-width": 2});
      this.scannerX = Math.min(1, (this.doneWindows || 0) / Math.max(1, this.windows || 0)) * width;
      scanner.style.transform = `translateX(${this.scannerX}px)`; node.append(scanner);
    }
    line({event, index}) {
      const row = el("div", "listening-entry"); row.dataset.event = event.type;
      row.append(el("p", "listening-voice", KeeperVoice.personality(this.id, event, index)), el("pre", "listening-process", KeeperVoice.process(event)));
      entries.append(row); entries.scrollTop = entries.scrollHeight;
    }
    pump() {
      if (this.timer !== null) return;
      if (!this.queue.length) { if (this.deadline) this.resolveDrain(); return; }
      const interval = this.queue.length > 8 ? 120 : 450;
      const delay = this.deadline ? Math.min(interval, Math.max(0, (this.deadline - performance.now()) / (this.queue.length + 1))) : interval;
      this.timer = setTimeout(() => { this.timer = null; const next = this.queue.shift(); if (next) this.line(next); this.pump(); }, delay);
    }
    flush() { clearTimeout(this.timer); this.timer = null; for (const entry of this.queue.splice(0)) this.line(entry); if (this.deadline) this.resolveDrain(); }
    skip() { this.skipped = true; this.flush(); for (const animation of this.animations) animation.cancel(); for (const clone of this.clones) clone.remove(); }
    cancel() { this.skip(); this.resolveDrain(); this.cleanup(); }
    cleanup() {
      if (this.phase === "finished") return;
      clearTimeout(this.timer); this.phase = "finished"; document.body.classList.remove("listening", "chopping");
      document.removeEventListener("click", this.input, true); document.removeEventListener("keydown", this.input, true);
      for (const animation of this.animations) animation.cancel(); for (const clone of this.clones) clone.remove();
      for (const row of document.querySelectorAll(".take-row")) row.style.removeProperty("opacity");
      $("take-count").textContent = state.takes.filter(t => t.section !== "rejected").length;
      document.querySelector(".recording h2").textContent = "Recording"; card.hidden = true;
      document.querySelector(".recording").style.removeProperty("transform-origin");
      if (active === this) active = null;
      renderRecording();
    }
    async reveal() {
      await this.drained;
      if (this.phase === "finished") return;
      this.phase = "chopping"; document.body.classList.add("chopping");
      try {
        if (this.skipped || reduced()) return;
        await this.animate(card, [{opacity: 1, transform: "translateY(0)"}, {opacity: 0, transform: "translateY(12px)"}], {duration: 300});
        if (this.skipped) return;
        const recording = document.querySelector(".recording"), first = recording.getBoundingClientRect();
        card.hidden = true; document.body.classList.remove("listening");
        document.querySelector(".recording h2").textContent = "Recording";
        // The same recording card occupies both layouts; FLIP animates its new geometry.
        renderRecording(); const last = recording.getBoundingClientRect();
        const bands = [...document.querySelectorAll(".recording-band")].sort((a, b) => Number(a.querySelector("rect").getAttribute("x")) - Number(b.querySelector("rect").getAttribute("x")));
        for (const band of bands) band.style.opacity = "0";
        const rows = [...document.querySelectorAll(".take-row")]; for (const row of rows) row.style.opacity = "0";
        $("take-count").textContent = "0";
        recording.style.transformOrigin = "top left";
        await this.animate(recording, [{transform: `translate(${first.left - last.left}px,${first.top - last.top}px) scale(${first.width / last.width},${first.height / last.height})`}, {transform: "none"}], {duration: 400});
        if (this.skipped) return;
        const takes = [...state.takes].sort((a, b) => a.start - b.start), limit = takes.length > 14 ? 10 : takes.length;
        const lights = bands.map((band, i) => this.animate(band, [{opacity: 0, transform: "translateY(0)"}, {opacity: 1, transform: "translateY(-4px)"}], {duration: 220, delay: Math.min(i, limit) * 70}).finally(() => band.style.removeProperty("opacity")));
        let count = 0;
        const flights = takes.map(async (take, index) => {
          const row = rows.find(node => node.dataset.take === take.id); if (!row) return;
          const target = take.section === "rejected" ? row.closest("details")?.querySelector("summary") || row : row;
          const to = target.getBoundingClientRect(), wave = $("timeline").getBoundingClientRect();
          const offscreen = to.top < 0 || to.bottom > innerHeight - document.querySelector(".transport").offsetHeight || to.width === 0;
          const delay = index < limit ? 250 + index * 90 : 250 + limit * 90;
          if (index < limit && !offscreen && !this.skipped) {
            const x = wave.left + take.start / state.data.duration_s * wave.width, width = Math.max(4, (take.end - take.start) / state.data.duration_s * wave.width);
            const clone = el("div", "chop-clone"); clone.style.cssText = `left:${x}px;top:${wave.top + 24}px;width:${width}px;height:74px;--section:${color(take.section)}`;
            // Crop an actual waveform slice in its original viewBox, without decoding audio.
            const segment = svg("svg", {viewBox: `${take.start / state.data.duration_s * recordWidth()} 24 ${width} 74`, preserveAspectRatio: "none"});
            for (const bar of $("timeline").querySelectorAll(":scope > line")) segment.append(bar.cloneNode());
            clone.append(segment); document.body.append(clone); this.clones.add(clone);
            const dx = to.left - x, dy = to.top - wave.top - 24;
            await this.animate(clone, [{opacity: 1, transform: "translate(0,-4px)"}, {opacity: .95, transform: `translate(${dx * .5}px,${dy * .5 - 32}px) scale(.85)`}, {opacity: take.section === "rejected" ? 0 : .4, transform: `translate(${dx}px,${dy}px) scale(${Math.min(1.6, to.width / width)},.6)`}], {duration: 520, delay});
            clone.remove(); this.clones.delete(clone);
          } else await this.animate(row, [{opacity: 0}, {opacity: 0}], {duration: 1, delay});
          row.style.removeProperty("opacity");
          if (!this.skipped && take.section !== "rejected") $("take-count").textContent = String(++count);
          await this.animate(row, [{opacity: 0, transform: "scale(.96)"}, {opacity: 1, transform: "scale(1)"}], {duration: 180});
        });
        await Promise.all([...lights, ...flights]);
      } finally { this.cleanup(); }
    }
  }

  async function peaksFromFile(file) {
    let context;
    try {
      context = new AudioContext(); const audio = await context.decodeAudioData(await file.arrayBuffer()), data = audio.getChannelData(0);
      return Array.from({length: 800}, (_, i) => { let peak = 0; const end = Math.floor((i + 1) * data.length / 800); for (let j = Math.floor(i * data.length / 800); j < end; j++) peak = Math.max(peak, Math.abs(data[j])); return Math.min(1, peak); });
    } catch (_) { return []; } finally { if (context) await context.close(); }
  }
  const baseRender = renderRecording;
  renderRecording = function() { if (active?.phase === "listening") active.draw(); else { baseRender(); memoMore.hidden = !state.memo; } };
  const baseLoad = loadMemo;
  loadMemo = async function(id) { if (active && active.id !== id) active.cancel(); await baseLoad(id); memoMore.hidden = !state.memo; };
  async function replay() {
    if (!state.memo) return;
    const id = state.memo, status = await json(`/api/memos/${id}/status`);
    if (!status.events?.length) { notice("This memo predates the process log. Upload a memo to record its events."); return; }
    const session = new Listening(id, state.data.peaks, true); session.feed(status); await session.reveal();
  }
  memoMore.onclick = () => {
    if (!state.memo) return;
    const items = [{label: "Replay reveal", run: replay}];
    if (canEdit()) items.push({label: "Rename memo...", run: beginMemoRename}, {label: "Delete memo...", run: () => $("delete").click()});
    openMenu(memoMore, items); menu?.classList.add("memo-menu");
  };
  $("file").onchange = async () => {
    const file = $("file").files[0]; if (!file) return;
    $("upload").disabled = true; let session;
    const peakPromise = peaksFromFile(file);
    try {
      const form = new FormData(); form.append("file", file);
      const now = new Date(); form.append("title", `Memo, ${now.toLocaleDateString("en-US", {month: "short", day: "numeric"})}, ${now.toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit"})}`);
      notice("Uploading memo…"); const result = await json("/api/memos", {method: "POST", body: form});
      session = new Listening(result.memo_id); notice("");
      $("memo-title").textContent = form.get("title"); $("memo-meta").textContent = file.name;
      $("memo-meta").classList.remove("empty-subtitle");
      peakPromise.then(peaks => { if (session.phase === "listening" && peaks.length) { session.peaks = peaks; session.draw(); } });
      while (true) {
        const status = await json(`/api/memos/${result.memo_id}/status`); session.feed(status);
        if (status.state === "failed") { await session.drained; throw Error(status.error || "Processing failed"); }
        if (status.state === "done") break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      // Load real rows while the last observations drain; editor rendering leaves the live wave in place.
      await listMemos(result.memo_id); await session.reveal();
    } catch (error) { const failed = session?.events.some(event => event.type === "failed"); session?.cancel(); renderRecording(); if (failed) card.hidden = false; notice(error.message, true); }
    finally { $("upload").disabled = false; $("file").value = ""; }
  };
  // URL replay runs once after the initial asynchronous memo selection completes.
  if (new URLSearchParams(location.search).get("reveal") === "1") {
    const preferred = new URLSearchParams(location.search).get("memo");
    const observer = new MutationObserver(() => { if (state.data && state.memo && $("take-list").children.length) { observer.disconnect(); attempt((async () => { if (preferred && preferred !== state.memo) await loadMemo(preferred); await replay(); })()); } });
    observer.observe($("take-list"), {childList: true});
  }
  window.KeeperProcessing = {Listening, replay, get active() { return active; }};
})();
