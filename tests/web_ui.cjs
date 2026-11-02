"use strict";
const {test} = require("node:test"), assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const {KeeperPlayer} = require("../app/static/audio.js");
const source = fs.readFileSync(require.resolve("../app/static/app.js"), "utf8");
const html = fs.readFileSync(require.resolve("../app/static/index.html"), "utf8");
function boot({blocked = false, memos = []} = {}) {
  class Element {
    constructor(text = "", value = "") { this.textContent = text; this.value = value; this.children = []; this.dataset = {}; this.attributes = {}; this.events = {}; this.classList = {toggle() {}}; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute(name, value) { this.attributes[name] = value; }
    querySelector() { return null; }
    focus() {}
  }
  const nodes = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const calls = [], storage = new Map(), token = "11111111-1111-4111-8111-111111111111";
  const context = vm.createContext({KeeperPlayer, Headers, console, performance:{now:() => 0},
    window:{addEventListener() {}}, navigator:{userAgent:"test", platform:"test", maxTouchPoints:0},
    crypto:{randomUUID:() => token}, localStorage:{getItem:key => { if (blocked) throw new Error("blocked"); return storage.get(key); }, setItem:(key, value) => storage.set(key, value)},
    document:{getElementById:id => { assert.ok(nodes[id], `missing element ${id}`); return nodes[id]; }, createElement:() => new Element(), createElementNS:() => new Element(), querySelectorAll:() => []},
    Option:Element, setTimeout, setInterval, clearInterval,
    fetch:async (url, options) => {
      calls.push({url, options});
      const body = url === "/api/memos" ? memos : url.endsWith("/status") ? {state:"done", history:[
        {stage:"queued", updated_at:"2026-11-01T00:00:00Z"}, {stage:"done", updated_at:"2026-11-01T00:01:05Z"}]} :
        {meta:memos.find(memo => url.endsWith(memo.id)), songmap:{sections:{}, parser:"spark"}, duration_s:10, regions:[], peaks:Array(800).fill(0)};
      return {ok:true, json:async () => body};
    }});
  vm.runInContext(source, context); return {context, nodes, calls, storage, token};
}
test("owned most-recent memo opens and options show only friendly titles", async () => {
  const memos = [{id:"public-id", title:"Showcase", showcase:true, owned:false, created_at:"2026-11-04"},
    {id:"own-old", title:"Older idea", owned:true, created_at:"2026-11-01"}, {id:"own-new", title:"Latest idea", owned:true, created_at:"2026-11-03"}];
  const h = boot({memos}); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.nodes["memo-title"].textContent, "Latest idea");
  assert.deepEqual(h.nodes["memo-picker"].children.map(option => option.textContent), ["Showcase", "Older idea", "Latest idea"]);
  assert.equal(h.nodes["processed-time"].textContent, "Processed in 1m 5s");
  assert.ok(h.calls.every(call => call.options.headers.get("X-Keeper-Owner") === h.token));
  assert.equal(h.storage.get("keeper-owner"), h.token);
});
test("showcase opens without owned memos, while unavailable localStorage keeps an in-memory token", async () => {
  const h = boot({blocked:true, memos:[{id:"public", title:"Public idea", showcase:true, owned:false, created_at:"2026-11-01"}]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.nodes["memo-title"].textContent, "Public idea"); assert.ok(h.nodes["privacy-hint"].textContent.includes("unavailable"));
  assert.ok(h.calls.every(call => call.options.headers.get("X-Keeper-Owner") === h.token));
});
test("detail smoothing merges short regions without mutating saved classifications", async () => {
  const h = boot(); await new Promise(resolve => setImmediate(resolve));
  const value = vm.runInContext(`(() => { const regions = [{start:0,end:3,kind:'music'},{start:3,end:3.5,kind:'speech'},{start:3.5,end:7,kind:'silence'}];
    const before = JSON.stringify(regions); const merged = visualRegions(regions); return {unchanged:before === JSON.stringify(regions), merged}; })()`, h.context);
  assert.equal(value.unchanged, true); assert.equal(value.merged.length, 2); assert.equal(value.merged[1].start, 3);
});
test("seek stays disabled until clip decoding completes", async () => {
  const h = boot(); await new Promise(resolve => setImmediate(resolve));
  vm.runInContext("player.onChange({mode:'loading',total:10,position:0,current:null,queue:[]})", h.context);
  assert.equal(h.nodes.seek.disabled, true);
  vm.runInContext("player.onChange({mode:'playing',total:10,position:1,current:null,queue:[]})", h.context);
  assert.equal(h.nodes.seek.disabled, false);
});
