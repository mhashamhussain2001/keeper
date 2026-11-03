"use strict";
(function(root){
  const sections=["intro","verse","pre-chorus","chorus","bridge","hook","outro","unsorted","unlabeled","rejected"];
  const title=section=>section==="unsorted"?"Ideas":section==="unlabeled"?"Unlabeled":section.charAt(0).toUpperCase()+section.slice(1);
  const seconds=value=>`${Math.floor(Math.max(0,value)/60)}:${Math.floor(Math.max(0,value)%60).toString().padStart(2,"0")}`;
  function visualRegions(regions){const result=regions.map(r=>({...r}));for(let i=0;i<result.length&&result.length>1;){if(result[i].end-result[i].start>=1.5){i++;continue;}const at=i===0?1:i===result.length-1?i-1:(result[i-1].end-result[i-1].start>=result[i+1].end-result[i+1].start?i-1:i+1);if(at<i)result[at].end=result[i].end;else result[at].start=result[i].start;result.splice(i,1);i=Math.max(0,i-1);}return result;}
  function canvasQueue(canvas,takes){
    const placed=canvas.clips.map((clip,i)=>({...clip,index:i,take:takes.find(t=>t.id===clip.take_id)})).filter(c=>c.take?.clip_url);
    const ends=[0,...placed.flatMap(c=>[c.start,c.start+c.take.end-c.take.start])].sort((a,b)=>a-b);
    const edges=[...new Set(ends)],queue=[];
    for(let i=0;i<edges.length-1;i++){const start=edges[i],end=edges[i+1];if(end<=start)continue;const active=placed.filter(c=>c.start<=start&&c.start+c.take.end-c.take.start>start).sort((a,b)=>a.lane-b.lane||a.index-b.index)[0];queue.push(active?{...active.take,id:`canvas-${active.index}-${i}`,name:active.take.name,context:"Canvas",take_id:active.take.id,start:0,end:end-start,slice_start:start-active.start,timelineStart:start}:{id:`gap-${i}`,name:"Silence",context:"Canvas",silence:true,start:0,end:end-start,timelineStart:start});}
    return queue;
  }
  root.KeeperUI={sections,title,seconds,visualRegions,canvasQueue};if(typeof module!=="undefined")module.exports=root.KeeperUI;
})(typeof window!=="undefined"?window:globalThis);
