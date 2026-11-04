"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict');
const {seconds,title,visualRegions,canvasQueue}=require('../app/static/ui_helpers.js');
test('display names and durations use sections and mono-ready times',()=>{
  assert.equal(title('unsorted'),'Ideas');assert.equal(seconds(376),'6:16');
});
test('detail smoothing preserves saved regions',()=>{
  const source=[{start:0,end:3,kind:'music'},{start:3,end:3.5,kind:'speech'},{start:3.5,end:7,kind:'silence'}];
  const before=JSON.stringify(source),result=visualRegions(source);
  assert.equal(JSON.stringify(source),before);assert.equal(result.length,2);assert.equal(result[1].start,3);
});
test('canvas overlap preserves every full clip and its source offset for mixing',()=>{
  const takes=[{id:'a',name:'Verse 1',start:10,end:14,clip_url:'/a.wav'},{id:'b',name:'Chorus 1',start:20,end:22,clip_url:'/b.wav'}];
  const queue=canvasQueue({clips:[{take_id:'a',start:1,lane:2},{take_id:'b',start:2,lane:0}]},takes);
  assert.deepEqual(queue.map(x=>[x.take_id,x.timelineStart,x.sourceOffset,x.end]),[['a',1,10,4],['b',2,20,2]]);
  assert.equal(queue.length,2);
});
