"use strict";
const {test} = require("node:test");
const assert = require("node:assert/strict");
const {KeeperPlayer} = require("../app/static/audio.js");
function harness(options = {}) {
  const events = [], sources = [], changes = [];
  const context = {state:"suspended", currentTime:0, sampleRate:16000, destination:{},
    resume() { events.push("resume"); this.state = "running"; return Promise.resolve(); },
    createBuffer() { return {duration:1 / 16000}; },
    createBufferSource() { const source = {connect() {}, disconnect() {}, stop() { this.stopped = true; }, start(when, offset = 0) { events.push("start"); this.when = when; this.offset = offset; }}; sources.push(source); return source; },
    decodeAudioData() { events.push("decode"); return Promise.resolve({duration:10}); }};
  const audio = {src:"", currentTime:0, play() { events.push("html-play"); return Promise.resolve(); }, pause() {}, removeAttribute() { this.src = ""; }};
  let creations = 0;
  const player = new KeeperPlayer({fetchClip: async () => { events.push("fetch"); return new ArrayBuffer(10); },
    contextFactory: () => { creations++; return context; }, audioFactory: () => audio,
    raf: () => 1, cancel() {}, objectURL: () => "blob:fixture", revokeURL() {}, onChange: change => changes.push(change), ...options});
  return {player, context, audio, events, sources, changes, creations:() => creations};
}
const take = {id:"a", clip_url:"/api/memos/private/clips/a.wav", section:"chorus", variant:"Take 1", start:0, end:10};
test("resume and one-sample unlock happen synchronously before fetch; one context survives taps", async () => {
  const h = harness(); assert.equal(h.creations(), 0);
  const playing = h.player.toggle([take]);
  assert.deepEqual(h.events.slice(0, 2), ["resume", "start"]); assert.equal(h.events.includes("fetch"), false);
  assert.equal(h.sources[0].buffer.duration, 1 / 16000); await playing;
  h.player.toggle([take]); await h.player.toggle([take]); assert.equal(h.creations(), 1);
});
test("pause retains offset, resume and seek restart sources at that offset", async () => {
  const h = harness(); await h.player.toggle([take]); h.context.currentTime = 4.04;
  h.player.toggle([take]); assert.equal(h.player.mode, "paused"); assert.equal(h.player.offset, 4);
  await h.player.toggle([take]); assert.equal(h.sources.at(-1).offset, 4);
  await h.player.seek(7); assert.equal(h.sources.at(-1).offset, 7); assert.equal(h.player.mode, "playing");
  h.player.stop(); assert.equal(h.player.position, 0); assert.ok(h.sources.at(-1).stopped);
});
test("back-to-back playback schedules contiguous buffers and reports the current take", async () => {
  const h = harness(), second = {...take, id:"b", clip_url:"/b.wav"};
  await h.player.toggle([take, second]);
  const scheduled = h.sources.slice(1); assert.equal(scheduled[1].when - scheduled[0].when, 10);
  h.context.currentTime = 12; assert.equal(h.player.current.id, "b");
  await h.player.seek(15); assert.equal(h.sources.at(-1).offset, 5);
});
test("interrupted and suspended states pause and resume on the next gesture", async () => {
  for (const state of ["interrupted", "suspended"]) {
    const h = harness(); await h.player.toggle([take]); h.context.currentTime = 2;
    h.context.state = state; h.context.onstatechange(); assert.equal(h.player.mode, "paused");
    await h.player.toggle([take]); assert.equal(h.context.state, "running"); assert.equal(h.player.mode, "playing");
  }
});
test("Web Audio failure fetches a blob for the single shared HTML fallback", async () => {
  const h = harness({contextFactory: () => { throw new Error("unsupported"); }});
  await h.player.toggle([take]); assert.equal(h.player.backend, "html"); assert.equal(h.audio.src, "blob:fixture");
  assert.equal(h.player.mode, "playing"); h.audio.currentTime = 3;
  h.player.toggle([take]); assert.equal(h.player.mode, "paused");
  await h.player.toggle([take]); assert.equal(h.audio.currentTime, 3);
  await h.player.seek(6); assert.equal(h.audio.currentTime, 6);
});
test("stopping during fetch cancels stale playback", async () => {
  let release;
  const h = harness({fetchClip: () => new Promise(resolve => { release = resolve; })});
  const pending = h.player.toggle([take]);
  while (!release) await new Promise(resolve => setImmediate(resolve));
  h.player.clear(); release(new ArrayBuffer(10)); await pending;
  assert.equal(h.player.mode, "stopped"); assert.equal(h.player.queue.length, 0);
});
