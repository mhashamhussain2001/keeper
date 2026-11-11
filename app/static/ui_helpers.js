"use strict";
(function(root){
  const sections=["intro","verse","pre-chorus","chorus","bridge","hook","outro","unsorted","unlabeled","rejected"];
  const title=section=>section==="unsorted"?"Ideas":section==="unlabeled"?"Takes":section.charAt(0).toUpperCase()+section.slice(1);
  const seconds=value=>`${Math.floor(Math.max(0,value)/60)}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,"0")}`;
  function visualRegions(regions){const result=regions.map(r=>({...r}));for(let i=0;i<result.length&&result.length>1;){if(result[i].end-result[i].start>=1.5){i++;continue;}const at=i===0?1:i===result.length-1?i-1:(result[i-1].end-result[i-1].start>=result[i+1].end-result[i+1].start?i-1:i+1);if(at<i)result[at].end=result[i].end;else result[at].start=result[i].start;result.splice(i,1);i=Math.max(0,i-1);}return result;}
  function canvasQueue(canvas,takes){
    const placed=canvas.clips.map((clip,i)=>({...clip,index:i,take:takes.find(t=>t.id===clip.take_id)})).filter(c=>c.take?.clip_url);
    return placed.sort((a,b)=>a.start-b.start||a.index-b.index).map(clip=>({...clip.take,
      id:`canvas-${clip.index}`,take_id:clip.take.id,context:"Canvas",timelineStart:clip.start,
      sourceOffset:clip.take.start,start:0,end:clip.take.end-clip.take.start,
      recording_url:clip.take.clip_url.replace(/\/clips\/.*$/,"/recording")}));
  }
  root.KeeperUI={sections,title,seconds,visualRegions,canvasQueue};if(typeof module!=="undefined")module.exports=root.KeeperUI;
})(typeof window!=="undefined"?window:globalThis);
