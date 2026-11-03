/* Gesture-unlocked Web Audio transport; API fetching is supplied by the UI. */
"use strict";
(function (root) {
  class KeeperPlayer {
    constructor({fetchClip, onChange = () => {}, onError = () => {}, onStart = () => {},
      contextFactory = () => new (root.AudioContext || root.webkitAudioContext)(),
      audioFactory = () => new root.Audio(), raf = callback => root.requestAnimationFrame(callback),
      cancel = id => root.cancelAnimationFrame(id), objectURL = blob => root.URL.createObjectURL(blob),
      revokeURL = url => root.URL.revokeObjectURL(url)} = {}) {
      Object.assign(this, {fetchClip, onChange, onError, onStart, contextFactory, audioFactory, raf, cancel, objectURL, revokeURL});
      this.context = null; this.audio = null; this.buffers = new Map(); this.blobs = new Map();
      this.queue = []; this.sources = []; this.lengths = []; this.offset = 0; this.mode = "stopped";
      this.backend = "web"; this.generation = 0; this.frame = null; this.startedAt = 0;
    }
    unlock() {
      // Called synchronously in the tap handler, before fetch/decode or any await.
      let resumed;
      try {
        if (!this.context) {
          this.context = this.contextFactory();
          this.context.onstatechange = () => {
            if (["interrupted", "suspended"].includes(this.context.state) && this.mode === "playing" && this.backend === "web") this.pause();
          };
        }
        resumed = Promise.resolve(this.context.resume());
        const source = this.context.createBufferSource();
        source.buffer = this.context.createBuffer(1, 1, this.context.sampleRate);
        source.connect(this.context.destination); source.onended = () => source.disconnect(); source.start(0);
      } catch (error) { resumed = Promise.reject(error); }
      // Prime the same HTML element during the gesture so an async Web Audio failure can fall back.
      try {
        if (!this.audio) {
          this.audio = this.audioFactory(); this.audio.playsInline = true;
          const silent = "data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA==";
          this.audio.src = silent;
          Promise.resolve(this.audio.play()).then(() => { if (this.audio.src === silent) this.audio.pause(); }).catch(() => {});
          this.audio.onended = () => { if (this.backend === "html") this.finish(); };
          this.audio.onpause = () => {
            if (this.backend === "html" && this.mode === "playing" && this.audio.paused && !this.audio.ended) { this.offset = this.audio.currentTime; this.mode = "paused"; this.emit(); }
          };
        }
      } catch (_) { /* Web Audio can still work if the fallback element cannot be created. */ }
      // Attach a handler immediately, including when this tap pauses rather than starts.
      resumed.catch(() => {});
      return resumed;
    }
    get total() { return this.lengths.reduce((sum, length) => sum + length, 0); }
    get position() {
      if (this.mode !== "playing") return this.offset;
      return Math.max(0, Math.min(this.total, this.backend === "html" ? this.audio.currentTime : Math.max(this.offset, this.context.currentTime - this.startedAt)));
    }
    get current() {
      let time = this.position;
      for (let i = 0; i < this.queue.length; i++) { if (time < this.lengths[i] || i === this.queue.length - 1) return this.queue[i]; time -= this.lengths[i]; }
      return null;
    }
    emit() { this.onChange({mode:this.mode, position:this.position, total:this.total, current:this.current, queue:this.queue}); }
    tick() {
      this.emit();
      if (this.mode === "playing") {
        if (this.position >= this.total) this.finish();
        else this.frame = this.raf(() => this.tick());
      }
    }
    haltSources() {
      for (const source of this.sources) { source.onended = null; try { source.stop(); } catch (_) {} source.disconnect(); }
      this.sources = []; if (this.frame !== null) this.cancel(this.frame); this.frame = null;
    }
    pause() {
      this.offset = this.position; this.mode = "paused"; this.haltSources();
      if (this.audio && this.backend === "html") this.audio.pause();
      this.emit();
    }
    stop() {
      this.generation++; this.mode = "stopped"; this.offset = 0; this.haltSources();
      if (this.audio) { this.audio.pause(); try { this.audio.currentTime = 0; } catch (_) {} }
      this.emit();
    }
    clear() {
      this.stop(); this.queue = []; this.lengths = []; this.buffers.clear();
      for (const url of this.blobs.values()) this.revokeURL(url);
      this.blobs.clear(); if (this.audio) this.audio.removeAttribute("src"); this.emit();
    }
    finish() { this.offset = this.total; this.mode = "ended"; this.haltSources(); this.emit(); }
    toggle(takes = this.queue) {
      const resumed = this.unlock();
      const same = takes.length === this.queue.length && takes.every((take, i) => take.id === this.queue[i].id);
      if (same && this.mode === "playing") { this.pause(); return Promise.resolve(); }
      const offset = same && this.mode === "paused" ? this.offset : 0;
      // A prepared HTML fallback resumes synchronously in this same user gesture.
      if (same && takes.length === 1 && this.backend === "html" && this.blobs.has(takes[0]?.clip_url)) {
        this.audio.currentTime = offset;
        const playing = this.audio.play(); this.mode = "playing"; this.offset = offset;
        return Promise.resolve(playing).then(() => { this.onStart(); this.tick(); }).catch(error => this.fail(error));
      }
      return this.start(takes, offset, resumed);
    }
    play(takes, offset = 0) {
      const resumed = this.unlock();
      return this.start(takes, offset, resumed);
    }
    seek(seconds) {
      const resumed = this.unlock();
      const offset = Math.max(0, Math.min(this.total, seconds));
      if (this.mode !== "playing") { this.offset = offset; if (this.mode === "ended") this.mode = "paused"; this.emit(); return Promise.resolve(); }
      if (this.backend === "html") { this.audio.currentTime = offset; this.offset = offset; this.emit(); return Promise.resolve(); }
      return this.start(this.queue, offset, resumed);
    }
    async start(takes, offset, resumed) {
      if (!takes.length) return;
      this.stop(); const generation = this.generation;
      this.queue = [...takes]; this.lengths = takes.map(take => take.end - take.start); this.offset = offset;
      this.mode = "loading"; this.emit();
      try {
        await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error("Audio context did not resume")), 2500);
          resumed.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
        });
        if (this.context.state !== "running") throw new Error("Audio context is interrupted");
        const buffers = await Promise.all(takes.map(async take => {
          if (take.silence) return this.context.createBuffer(1, Math.max(1, Math.round((take.end - take.start) * this.context.sampleRate)), this.context.sampleRate);
          let buffer = this.buffers.get(take.clip_url);
          if (!buffer) {
            const bytes = await this.fetchClip(take.clip_url);
            buffer = await this.context.decodeAudioData(bytes.slice(0));
            if (generation === this.generation) this.buffers.set(take.clip_url, buffer);
          }
          if (take.slice_start === undefined) return buffer;
          const begin = Math.round(take.slice_start * buffer.sampleRate);
          const count = Math.max(1, Math.min(buffer.length - begin, Math.round((take.end - take.start) * buffer.sampleRate)));
          const slice = this.context.createBuffer(buffer.numberOfChannels, count, buffer.sampleRate);
          for (let channel = 0; channel < buffer.numberOfChannels; channel++) slice.copyToChannel(buffer.getChannelData(channel).subarray(begin, begin + count), channel);
          return slice;
        }));
        if (generation !== this.generation) return;
        this.backend = "web"; this.lengths = buffers.map(buffer => buffer.duration);
        const now = this.context.currentTime + 0.04; this.startedAt = now - offset;
        let cursor = 0;
        buffers.forEach(buffer => {
          const end = cursor + buffer.duration;
          if (end > offset) {
            const source = this.context.createBufferSource(); source.buffer = buffer;
            source.connect(this.context.destination);
            source.start(now + Math.max(0, cursor - offset), Math.max(0, offset - cursor)); this.sources.push(source);
          }
          cursor = end;
        });
        this.mode = "playing"; this.onStart(); this.tick();
      } catch (error) {
        if (generation !== this.generation) return;
        if (takes.length !== 1) { this.fail(new Error("Back-to-back playback needs Web Audio. Try playing one take.")); return; }
        await this.fallback(takes[0], offset, generation);
      }
    }
    async fallback(take, offset, generation) {
      try {
        if (!this.audio) throw new Error("Audio playback is unavailable");
        let url = this.blobs.get(take.clip_url);
        if (!url) {
          const bytes = await this.fetchClip(take.clip_url);
          if (generation !== this.generation) return;
          url = this.objectURL(new Blob([bytes], {type:"audio/wav"})); this.blobs.set(take.clip_url, url);
        }
        if (generation !== this.generation) return;
        this.backend = "html"; this.audio.src = url; this.audio.currentTime = offset;
        this.mode = "playing"; await this.audio.play();
        if (generation !== this.generation) { this.audio.pause(); return; }
        this.onStart(); this.tick();
      } catch (error) { if (generation === this.generation) { this.mode = "paused"; this.emit(); this.onError("Tap play again to start audio. " + error.message); } }
    }
    fail(error) { this.mode = "paused"; this.haltSources(); this.emit(); this.onError(error.message); }
  }
  root.KeeperPlayer = KeeperPlayer;
  if (typeof module !== "undefined") module.exports = {KeeperPlayer};
})(typeof window !== "undefined" ? window : globalThis);
