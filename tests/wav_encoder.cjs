"use strict";
const {test}=require('node:test'),assert=require('node:assert/strict');
const {encodeWav}=require('../app/static/wav_encoder.js');

test('PCM16 WAV contains correct RIFF fields, interleaved frames and signed samples',async()=>{
  const result=await encodeWav([new Float32Array([0,.5,-.5]),new Float32Array([.25,-.25,0])],48000);
  const view=new DataView(result.bytes);
  assert.equal(Buffer.from(result.bytes).toString('ascii',0,4),'RIFF');
  assert.equal(Buffer.from(result.bytes).toString('ascii',8,16),'WAVEfmt ');
  for(const [offset,value,size]of [[4,48,4],[16,16,4],[20,1,2],[22,2,2],[24,48000,4],[28,192000,4],[32,4,2],[34,16,2],[40,12,4]])
    assert.equal(size===2?view.getUint16(offset,true):view.getUint32(offset,true),value);
  assert.equal(result.frames,3);assert.equal(result.bytes.byteLength,56);assert.equal(result.gain,1);
  assert.deepEqual(Array.from({length:6},(_,i)=>view.getInt16(44+i*2,true)),[0,8192,16384,-8192,-16384,0]);
});

test('one flat attenuation preserves channel balance and puts an excessive peak at -1 dBFS',async()=>{
  const result=await encodeWav([new Float32Array([2,1]),new Float32Array([1,.5])],44100);
  assert.ok(Math.abs(result.gain-Math.pow(10,-1/20)/2)<1e-9);
  const view=new DataView(result.bytes);
  assert.ok(Math.abs(view.getInt16(44,true)/32767-Math.pow(10,-1/20))<1/32767);
  assert.ok(Math.abs(view.getInt16(44,true)/view.getInt16(46,true)-2)<.0001);
});
