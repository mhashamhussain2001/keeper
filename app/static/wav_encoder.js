/* PCM16 RIFF encoder, shared by the worker and the cooperative main-thread fallback. */
"use strict";
(function(root) {
  async function encodeWav(channels, sampleRate, yieldFrame = () => Promise.resolve()) {
    if (!channels.length || !channels.every(channel => channel.length === channels[0].length)) throw Error("Invalid audio channels");
    const frames = channels[0].length, count = channels.length, size = frames * count * 2;
    if (size > 0xffffffff - 36) throw Error("Arrangement is too large for a WAV file");
    let peak = 0;
    for (let start = 0; start < frames; start += 65536) {
      for (const channel of channels) for (let i = start; i < Math.min(frames, start + 65536); i++) peak = Math.max(peak, Math.abs(channel[i]));
      await yieldFrame();
    }
    const gain = peak > .99 ? Math.pow(10, -1 / 20) / peak : 1;
    const bytes = new ArrayBuffer(44 + size), view = new DataView(bytes);
    for (const [offset, value] of [[0,"RIFF"],[8,"WAVE"],[12,"fmt "],[36,"data"]])
      for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
    view.setUint32(4, 36 + size, true); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, count, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * count * 2, true);
    view.setUint16(32, count * 2, true); view.setUint16(34, 16, true); view.setUint32(40, size, true);
    for (let start = 0; start < frames; start += 65536) {
      for (let i = start; i < Math.min(frames, start + 65536); i++) for (let channel = 0; channel < count; channel++) {
        const value = Math.max(-1, Math.min(1, channels[channel][i] * gain));
        view.setInt16(44 + (i * count + channel) * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
      }
      await yieldFrame();
    }
    return {bytes, gain, peak, frames, sampleRate, channels:count};
  }
  root.KeeperWav = {encodeWav};
  if (typeof module !== "undefined") module.exports = root.KeeperWav;
  if (typeof document === "undefined" && typeof root.postMessage === "function") root.onmessage = async event => {
    try { const result = await encodeWav(event.data.channels, event.data.sampleRate); root.postMessage(result, [result.bytes]); }
    catch (error) { root.postMessage({error:error.message}); }
  };
})(typeof self !== "undefined" ? self : globalThis);
