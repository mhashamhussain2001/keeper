/* Shared clip envelope and offline mixing keep Canvas playback and export in agreement. */
"use strict";
(function(root) {
  function scheduleClip(context, buffer, {when, offset = 0, duration, elapsed = 0}) {
    const source = context.createBufferSource(), gain = context.createGain();
    source.buffer = buffer; source.connect(gain); gain.connect(context.destination);
    const fade = Math.min(.005, duration / 2), remaining = duration - elapsed;
    gain.gain.setValueAtTime(fade ? Math.max(0, Math.min(1, elapsed / fade, remaining / fade)) : 1, when);
    if (elapsed < fade) gain.gain.linearRampToValueAtTime(1, when + fade - elapsed);
    if (remaining > fade) gain.gain.setValueAtTime(1, when + remaining - fade);
    gain.gain.linearRampToValueAtTime(0, when + remaining);
    source.start(when, offset + elapsed, remaining);
    source.onended = () => { source.disconnect(); gain.disconnect(); };
    source.keeperGain = gain;
    return source;
  }
  async function renderMix(clips, sampleRate, channels, length) {
    const context = new root.OfflineAudioContext(channels, Math.max(1, Math.ceil(length * sampleRate)), sampleRate);
    for (const clip of clips) scheduleClip(context, clip.buffer, {
      when:clip.timelineStart || 0, offset:clip.offset || 0, duration:clip.duration,
    });
    return context.startRendering();
  }
  const yieldFrame = () => new Promise(resolve => setTimeout(resolve, 0));
  async function writeWav(buffer) {
    const channels = [];
    for (let i = 0; i < buffer.numberOfChannels; i++) { channels.push(buffer.getChannelData(i).slice()); await yieldFrame(); }
    let result;
    if (root.Worker) {
      try {
        result = await new Promise((resolve, reject) => {
          const worker = new root.Worker("/wav_encoder.js");
          const timer = setTimeout(() => { worker.terminate(); reject(Error("WAV worker timed out")); }, 120000);
          const finish = () => { clearTimeout(timer); worker.terminate(); };
          worker.onmessage = event => { finish(); event.data.error ? reject(Error(event.data.error)) : resolve(event.data); };
          worker.onerror = event => { finish(); reject(Error(event.message || "WAV worker unavailable")); };
          // Copies retain a usable main-thread fallback if the worker fails after receiving data.
          worker.postMessage({channels, sampleRate:buffer.sampleRate});
        });
      } catch (_) { /* CSP/worker support may require cooperative encoding on the main thread. */ }
    }
    result ||= await root.KeeperWav.encodeWav(channels, buffer.sampleRate, yieldFrame);
    if (result.gain !== 1) console.info(`Keeper export: applied flat gain ${result.gain.toFixed(6)} (${(20 * Math.log10(result.gain)).toFixed(2)} dB); peak ${result.peak.toFixed(4)} → -1 dBFS`);
    return result;
  }
  root.KeeperAudio = {scheduleClip, renderMix, writeWav, yieldFrame};
  if (typeof module !== "undefined") module.exports = root.KeeperAudio;
})(typeof window !== "undefined" ? window : globalThis);
