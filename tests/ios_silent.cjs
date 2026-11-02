"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const html = fs.readFileSync(require.resolve("../app/static/index.html"), "utf8");
const hook = html.match(/<script id="ios-audio-session">([\s\S]*?)<\/script>/)[1];
const audioSource = fs.readFileSync(require.resolve("../app/static/audio.js"), "utf8");
function harness({session = false, setterThrows = false, loopRejects = false} = {}) {
  const events = [], loops = [], hint = {hidden:false};
  class Audio {
    constructor() { this.paused = true; this.src = ""; }
    setAttribute() {}
    removeAttribute() {}
    play() { events.push(this.loop ? "loop-play" : "prime-play"); this.paused = false; return this.loop && loopRejects ? Promise.reject(new Error("blocked")) : Promise.resolve(); }
    pause() { this.paused = true; events.push(this.loop ? "loop-pause" : "prime-pause"); }
  }
  let type = "auto";
  const navigator = session ? {audioSession:{get type() { return type; }, set type(value) { events.push("session:" + value); if (setterThrows) throw new Error("unsupported"); type = value; }}} : {};
  const context = vm.createContext({navigator, document:{getElementById:() => hint, body:{append() {}}, createElement:() => { const audio = new Audio(); loops.push(audio); return audio; }},
    window:{Audio}, Uint8Array, DataView, Promise, setTimeout, clearTimeout, Blob,
    btoa:binary => Buffer.from(binary, "binary").toString("base64")});
  vm.runInContext(audioSource, context); vm.runInContext(hook, context);
  const audioContext = {state:"running", currentTime:0, sampleRate:16000, destination:{},
    resume() { events.push("resume"); return Promise.resolve(); }, createBuffer() { return {duration:1 / 16000}; },
    createBufferSource() { return {connect() {}, disconnect() {}, start() {}, stop() {}}; }, decodeAudioData() { return Promise.resolve({duration:10}); }};
  let release;
  const player = new context.window.KeeperPlayer({contextFactory:() => audioContext, audioFactory:() => new Audio(), raf:() => 1, cancel() {},
    fetchClip:() => { events.push("fetch"); return new Promise(resolve => { release = resolve; }); }});
  return {player, events, loops, hint, release:() => release(new ArrayBuffer(10))};
}
const take = {id:"a", clip_url:"/a.wav", start:0, end:10};
test("AudioSession playback is attempted synchronously before AudioContext resume and fetching", async () => {
  const h = harness({session:true}), pending = h.player.toggle([take]);
  assert.deepEqual(h.events.slice(0, 2), ["session:playback", "resume"]); assert.equal(h.events.includes("fetch"), false);
  await new Promise(resolve => setImmediate(resolve)); h.release(); await pending;
  assert.equal(h.loops.length, 0); assert.equal(h.hint.hidden, true);
});
test("fallback loops genuinely silent WAV at normal volume through loading, pauses on stop, restarts on tap", async () => {
  const h = harness(), pending = h.player.toggle([take]);
  assert.equal(h.events[0], "loop-play"); assert.equal(h.events.includes("resume"), true);
  const loop = h.loops[0]; assert.equal(loop.loop, true); assert.equal(loop.volume, 1); assert.equal(loop.muted, false); assert.equal(loop.playsInline, true);
  const wav = Buffer.from(loop.src.split(",")[1], "base64");
  assert.equal(wav.toString("ascii", 0, 4), "RIFF"); assert.equal(wav.readUInt32LE(24), 16000);
  assert.equal(wav.readUInt32LE(40), 3200); assert.ok(wav.subarray(44).every(byte => byte === 0));
  assert.equal(loop.paused, false); // start()'s internal stop must not end the workaround.
  await new Promise(resolve => setImmediate(resolve)); h.release(); await pending;
  assert.equal(loop.paused, false); assert.equal(h.hint.hidden, true);
  h.player.stop(); assert.equal(loop.paused, true);
  const restarting = h.player.toggle([take]); assert.equal(loop.paused, false); await restarting;
  h.player.pause(); assert.equal(loop.paused, true);
  await h.player.toggle([take]);
  assert.equal(typeof h.player.sources.at(-1).onended, "function");
  h.player.sources.at(-1).onended(); // No animation frame is needed for natural completion.
  assert.equal(loop.paused, true); assert.equal(h.player.mode, "ended");
});
test("a rejected AudioSession setter uses the looping fallback", async () => {
  const h = harness({session:true, setterThrows:true}), pending = h.player.toggle([take]);
  assert.equal(h.events[0], "session:playback"); assert.equal(h.events[1], "loop-play");
  await new Promise(resolve => setImmediate(resolve)); h.release(); await pending; h.player.stop();
});
test("silent-switch hint is retained only when both routes fail", async () => {
  const h = harness({loopRejects:true}), pending = h.player.toggle([take]);
  await new Promise(resolve => setImmediate(resolve)); h.release(); await pending;
  h.hint.hidden = false; h.player.emit(); assert.equal(h.hint.hidden, false); h.player.stop();
});
