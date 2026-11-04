"use strict";
/* Download actions take an immutable snapshot, so switching memos cannot mix recordings. */
let exportStage = "";
function exportFilename(value) {
  let name = value.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g,"_").replace(/^[ .]+|[ .]+$/g,"").slice(0,150).replace(/[ .]+$/g,"") || "Untitled";
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i.test(name)) name = "_" + name;
  return name;
}
function exportToast(message, error=false) {
  clearTimeout(toastTimer); toast.replaceChildren(el("span","",message)); toast.hidden=false;
  toast.setAttribute("role",error?"alert":"status"); toastTimer=setTimeout(()=>toast.hidden=true,12000);
}
function exportProgress(stage) {
  exportStage=stage; $("export").textContent=stage||"Export";
  for (const node of [$("export"),exportControl]) { setDisabled(node,!!stage||!state.data); node.setAttribute("aria-busy",String(!!stage)); }
  exportControl.title=stage||"Export audio";
  exportControl.setAttribute("aria-label",stage||"Export audio");
  exportControl.querySelector(".export-progress-label")?.remove();
  if(stage){const label=el("span","export-progress-label",stage);label.setAttribute("role","status");exportControl.append(label);}
}
function downloadExport(blob, filename) {
  const url=URL.createObjectURL(blob), link=el("a"); link.href=url; link.download=filename;
  document.body.append(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),60000);
}
function selectedExportTake() {
  const selected=state.canvas.clips[state.selected];
  return state.takes.find(take=>take.id===(selected?.take_id || (state.exportTakeMemo===state.memo?state.exportTakeId:null)))
    || state.takes.find(take=>take.id===(playback.current?.take_id||playback.current?.id));
}
async function exportAudio(singleTake=null) {
  if (exportStage) return;
  if (!singleTake && state.data.duration_s > 20 * 60) { exportToast("Use All takes (ZIP) for long memos"); return; }
  const memo=state.memo, memoTitle=state.data.meta.title;
  const clips=singleTake?[{...singleTake,timelineStart:0}]:canvasQueue(state.canvas,state.takes).map(take=>({...take,start:take.sourceOffset,end:take.sourceOffset+take.end-take.start}));
  if (!clips.length) return;
  const length=Math.max(...clips.map(clip=>clip.timelineStart+clip.end-clip.start))+(singleTake?0:.5);
  if (!singleTake && length>600 && !confirm("This arrangement is longer than 10 minutes and may use a lot of memory. Export it?")) return;
  let fallback=false, fallbackReason="";
  try {
    const originalURL=`/api/memos/${memo}/recording`;
    let result, renderMs;
    for (const original of [true,false]) {
      try {
        exportProgress("Decoding..."); await KeeperAudio.yieldFrame();
        const source=original?await player.loadBuffer(originalURL):null;
        const sources=source?clips.map(clip=>({buffer:source,offset:clip.start,duration:clip.end-clip.start,timelineStart:clip.timelineStart}))
          :await Promise.all(clips.map(async clip=>({buffer:await player.loadBuffer(clip.clip_url),offset:0,duration:clip.end-clip.start,timelineStart:clip.timelineStart})));
        exportProgress("Rendering..."); await KeeperAudio.yieldFrame();
        const renderStarted=performance.now();
        const rendered=await KeeperAudio.renderMix(sources,source?.sampleRate||16000,source?.numberOfChannels||1,length);
        renderMs=performance.now()-renderStarted;
        exportProgress("Writing file..."); await KeeperAudio.yieldFrame();
        result=await KeeperAudio.writeWav(rendered);
        downloadExport(new Blob([result.bytes],{type:"audio/wav"}),exportFilename(singleTake?singleTake.name:memoTitle+" - arrangement")+".wav");
        break;
      } catch (error) {
        if (!original) throw error;
        fallback=true; fallbackReason=error.message;
        exportToast(`Original-quality export failed: ${fallbackReason}. Using 16 kHz preview clips.`,true);
      }
    }
    window.keeper.lastExport={renderMs,bytes:result.bytes.byteLength,sampleRate:result.sampleRate,channels:result.channels,duration:result.frames/result.sampleRate,gain:result.gain,fallback};
    exportToast(fallback?`Exported from 16 kHz preview clips. Original-quality export failed: ${fallbackReason}`:"WAV exported at the original recording's quality.");
  } catch (error) { exportToast(`Export failed: ${error.message}. Try fewer clips or export stems.`,true); }
  finally { exportProgress(""); }
}
async function exportStems() {
  if (exportStage) return;
  const memo=state.memo,name=state.data.meta.title;
  try {
    exportProgress("Writing file..."); await saveTail;
    const response=await api(`/api/memos/${memo}/export/stems`);
    downloadExport(await response.blob(),exportFilename(name)+" - stems.zip"); exportToast("Stems exported at the original recording's quality.");
  } catch (error) { exportToast(`Stems export failed: ${error.message}`,true); }
  finally { exportProgress(""); }
}
function openExportMenu(anchor) {
  if (exportStage) return;
  const take=selectedExportTake(), items=[
    {label:"Arrangement (WAV)",disabledReason:state.data.duration_s>20*60?"Use All takes (ZIP) for long memos":!state.canvas.clips.length?"Add takes to the canvas first.":"",run:()=>exportAudio()},
    {label:"All takes (ZIP)",disabledReason:!state.takes.some(take=>take.section!=="rejected")?"There are no takes to export.":!state.data?.meta.owned?"Only the memo owner can export stems.":"",run:exportStems},
  ];
  if (take) items.push({label:"This take (WAV)",run:()=>exportAudio({...take})});
  openMenu(anchor,items);
}
$("export").hidden=false;
for (const node of [$("export"),exportControl]) {
  node.setAttribute("aria-haspopup","menu"); node.setAttribute("aria-expanded","false");
  node.onclick=()=>openExportMenu(node);
  node.addEventListener("keydown",event=>{if(event.code==="Space")event.stopPropagation();});
}
exportControl.setAttribute("aria-label","Export audio");
document.addEventListener("focusin",event=>{const row=event.target.closest(".take-row");if(row){state.exportTakeMemo=state.memo;state.exportTakeId=row.dataset.take;}});
document.addEventListener("pointerdown",event=>{const row=event.target.closest(".take-row");if(row){state.exportTakeMemo=state.memo;state.exportTakeId=row.dataset.take;}});
const exportPaintIcons=paintIcons;
paintIcons=function(){exportPaintIcons();exportProgress(exportStage);};
exportProgress("");
