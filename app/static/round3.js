"use strict";
/* Memo actions, measured recording labels, saved command analysis and peak-only clip waveforms. */
const headingRow=el("div","memo-title-row"),memoMore=el("button","circle memo-more"),renameInput=$("rename-input");
memoMore.id="memo-more";memoMore.setAttribute("aria-label","Memo actions");memoMore.setAttribute("aria-haspopup","menu");setIcon(memoMore,"more-horizontal");
$("memo-title").before(headingRow);headingRow.append($("memo-title"),renameInput,memoMore);$("memo-actions").hidden=true;
let renameState=null;
function beginMemoRename(){if(!canEdit())return;renameState={id:state.memo,title:state.data.meta.title};$("memo-title").hidden=true;renameInput.hidden=false;renameInput.value=renameState.title;renameInput.maxLength=120;renameInput.focus();renameInput.select();}
function finishMemoRename(save){
  if(!renameState)return;const previous=renameState,name=renameInput.value.trim();renameState=null;renameInput.hidden=true;$("memo-title").hidden=false;
  if(!save||!name||name===previous.title){memoMore.focus();return;}
  attempt(json(`/api/memos/${previous.id}`,send("PATCH",{title:name})).then(()=>{
    const memo=state.memos.find(row=>row.id===previous.id);if(memo)memo.title=name;
    if(state.memo===previous.id){state.data.meta.title=name;$("memo-title").textContent=name;$("playing-memo").textContent=name;}
    const item=document.querySelector(`[data-memo="${previous.id}"] span`);if(item)item.textContent=name;
    for(const option of $("memo-picker").options)if(option.value===previous.id)option.textContent=name;
  }));
}
renameInput.onkeydown=event=>{if(["Enter","Escape"].includes(event.key)){event.preventDefault();event.stopPropagation();finishMemoRename(event.key==="Enter");}};renameInput.onblur=()=>finishMemoRename(true);
memoMore.onclick=()=>{if(!canEdit())return;openMenu(memoMore,[{label:"Rename memo...",run:beginMemoRename},{label:"Delete memo...",run:()=>$("delete").click()}]);menu?.classList.add("memo-menu");const buttons=menu?.querySelectorAll("button");if(buttons?.[1]){buttons[1].classList.add("danger-text","menu-separated");}};
const oldCloseDelete=closeDelete;closeDelete=function(){oldCloseDelete();if(!deleting)memoMore.focus();};
$("cancel-delete").onclick=closeDelete;
const priorLoad=loadMemo;loadMemo=async function(id){renameState=null;renameInput.hidden=true;$("memo-title").hidden=false;await priorLoad(id);$("memo-actions").hidden=true;memoMore.hidden=!canEdit();syncAnalysis();};
const priorEmpty=emptyMemo;emptyMemo=function(){priorEmpty();memoMore.hidden=true;};memoMore.hidden=!canEdit();

const analysisControl=$("show-detail"),analysisLabel=analysisControl.parentElement;
analysisLabel.replaceChildren(analysisControl,document.createTextNode("Show analysis"));
let analysisPreferred=false;try{analysisPreferred=localStorage.getItem("keeper-analysis")==="true";}catch(_){}
const commandCard=el("div","command-card");commandCard.id="command-card";commandCard.role="tooltip";commandCard.hidden=true;document.body.append(commandCard);
function hideCommand(){commandCard.hidden=true;}
function showCommand(pin,command){
  commandCard.replaceChildren(el("strong","",`“${command.heard}”`));
  const filed=command.take_ids.map(id=>state.takes.find(t=>t.id===id)?.name||id).join(", ")||"No take filed";
  for(const[label,value]of[["Intent",command.intent],["Section",command.section||"—"],["Target",command.target],["Confidence",`${Math.round(command.confidence*100)}%`],["Filed take",filed]]){const row=el("div","command-card-row");row.append(el("span","muted",label),el("span","",value));commandCard.append(row);}
  commandCard.hidden=false;const box=pin.getBoundingClientRect(),size=commandCard.getBoundingClientRect();commandCard.style.left=`${Math.max(8,Math.min(innerWidth-size.width-8,box.left))}px`;commandCard.style.top=`${Math.max(8,Math.min(innerHeight-size.height-8,box.bottom+5))}px`;
}
document.addEventListener("scroll",hideCommand,{capture:true,passive:true});document.addEventListener("keydown",event=>{if(event.key==="Escape")hideCommand();});document.addEventListener("pointerdown",event=>{if(!event.target.closest(".command-pin"))hideCommand();});
function syncAnalysis(){const available=!!state.data?.regions?.length&&!!state.data?.analysis?.commands?.length;analysisLabel.hidden=!available;analysisControl.checked=available&&analysisPreferred;renderRecording();}
analysisControl.onchange=()=>{analysisPreferred=analysisControl.checked;try{localStorage.setItem("keeper-analysis",String(analysisPreferred));}catch(_){}renderRecording();};
const measureCanvas=document.createElement("canvas"),measureContext=measureCanvas.getContext("2d");
function labelLayout(takes,width,duration){
  measureContext.font='400 11px Figtree';const ends=[-4,-4],labels=[];
  for(const take of [...takes].sort((a,b)=>a.start-b.start)){
    const bandX=take.start/duration*width,bandWidth=(take.end-take.start)/duration*width,full=take.name,fullWidth=measureContext.measureText(full).width;
    const abbreviation=(full.match(/[A-Za-z]/)?.[0]||"T").toUpperCase()+(full.match(/\d+$/)?.[0]||String(takes.indexOf(take)+1));
    let placed=false;const choices=fullWidth<=bandWidth+8?[[full,0],[full,1],[abbreviation,0],[abbreviation,1]]:[[abbreviation,0],[abbreviation,1]];
    for(const[text,row]of choices){
      const textWidth=measureContext.measureText(text).width;
      const x=Math.max(0,Math.min(width-textWidth,bandX+2));
      if(textWidth<=width&&x>=ends[row]+4){labels.push({take,text,row,x,width:textWidth});ends[row]=x+textWidth;placed=true;break;}
    }
    // Dense bands still expose their full names through their focusable band tooltip.
    if(!placed)continue;
  }return labels;
}
function commandLabels(width,duration){const ends=[],result=[];for(const command of state.data.analysis.commands){const x=command.start/duration*width,text=`“${command.heard.slice(0,24)}”`,tw=measureContext.measureText(text).width+8,labelX=Math.max(0,Math.min(width-tw,x+7));let row=ends.findIndex(end=>labelX>=end+4);if(row<0)row=ends.length;ends[row]=labelX+tw;result.push({command,x,text,labelX,row});}return result;}
renderRecording=function(){
  const available=!!state.data?.regions?.length&&!!state.data?.analysis?.commands?.length;analysisLabel.hidden=!available;analysisControl.checked=available&&analysisPreferred;memoMore.hidden=!canEdit();
  hideCommand();const node=$("timeline"),width=recordWidth(),duration=state.data?.duration_s||1,takes=state.takes.filter(t=>t.section!=="rejected"),labels=labelLayout(takes,width,duration),extra=labels.some(label=>label.row===1)?14:0;
  const analysis=analysisControl.checked&&!analysisLabel.hidden,commands=analysis?commandLabels(width,duration):[],commandRows=Math.max(0,...commands.map(c=>c.row))+1,analysisExtra=analysis?61+commandRows*14:0,height=151+extra+analysisExtra,waveTop=24+extra,waveMid=61+extra,waveBottom=98+extra;
  document.querySelector(".recording").style.height=`${(matchMedia('(max-width:899px)').matches?240:237)+extra+analysisExtra}px`;
  node.style.width=`${width}px`;node.style.height=`${height}px`;node.setAttribute("viewBox",`0 0 ${width} ${height}`);node.replaceChildren();document.querySelector(".recording").classList.toggle("label-row-two",!!extra);document.querySelector(".recording").classList.toggle("analysis-on",analysis);
  for(const take of takes){const x=take.start/duration*width,w=(take.end-take.start)/duration*width,band=svg("g",{class:"recording-band",tabindex:0,role:"img","aria-label":take.name});band.append(svg("title",{},take.name),svg("rect",{x,y:waveTop,width:w,height:74,rx:4,fill:color(take.section)}));node.append(band);}
  const peaks=state.data?.peaks||Array(800).fill(0);
  for(let i=0;i<peaks.length;i++){const time=i/peaks.length*duration,x=(i+.5)/peaks.length*width,h=Math.max(2,Math.min(64,peaks[i]*70)),active=takes.some(t=>time>=t.start&&time<=t.end);node.append(svg("line",{x1:x,x2:x,y1:waveMid-h/2,y2:waveMid+h/2,stroke:active?"var(--wave-active)":"var(--wave)","stroke-width":Math.max(1,width/peaks.length*.55),"stroke-linecap":"round"}));}
  for(const label of labels)node.append(svg("text",{class:"recording-label",x:label.x,y:label.row===0?15:29},label.text));
  if(analysis){
    const y=waveBottom+10;for(const region of state.data.regions){const x=region.start/duration*width,w=(region.end-region.start)/duration*width;
      node.append(region.kind==="silence"?svg("line",{x1:x,x2:x+w,y1:y+4,y2:y+4,stroke:"var(--text-muted)","stroke-dasharray":"2 3",class:"analysis-region"}):svg("rect",{x,y,width:w,height:8,rx:2,fill:region.kind==="speech"?"var(--text-muted)":"var(--accent)",opacity:region.kind==="speech"?1:.7,class:"analysis-region"}));}
    for(const take of takes)for(const time of[take.start,take.end])node.append(svg("line",{x1:time/duration*width,x2:time/duration*width,y1:waveTop,y2:y+9,stroke:color(take.section),"stroke-width":1,class:"take-boundary"}));
    for(const{command,x,text,labelX,row}of commands){const pin=svg("g",{class:"command-pin",tabindex:0,role:"button","aria-label":`Command: ${command.heard}`});pin.append(svg("line",{x1:x,x2:x,y1:y+16,y2:y+28,stroke:"var(--accent)"}),svg("circle",{cx:x,cy:y+17,r:4,fill:command.intent==="none"?"var(--surface)":"var(--accent)",stroke:"var(--accent)","stroke-width":1.5}));
      pin.append(svg("text",{x:labelX,y:y+29+row*14,class:"command-label"},text));
      pin.onpointerenter=()=>showCommand(pin,command);pin.onpointerleave=hideCommand;pin.onfocus=()=>showCommand(pin,command);pin.onblur=hideCommand;pin.onclick=event=>{event.stopPropagation();showCommand(pin,command);};pin.onkeydown=event=>{if(["Enter"," "].includes(event.key)){event.preventDefault();showCommand(pin,command);}};node.append(pin);
    }
    for(const[index,text]of["Singing","Speech","Silence"].entries()){const x=Math.max(0,width-190)+index*66,legendY=y+40+commandRows*14;node.append(svg("circle",{cx:x,cy:legendY,r:3,fill:index===0?"var(--accent)":index===1?"var(--text-muted)":"none",stroke:"var(--text-muted)"}),svg("text",{x:x+7,y:legendY+3,class:"analysis-legend"},text));}
  }else for(const region of state.data?.regions||[])if(region.kind==="speech")node.append(svg("circle",{cx:region.start/duration*width,cy:107+extra,r:3,fill:"var(--accent)"}));
  const step=duration<60?10:60;for(let t=0;t<duration;t+=step)node.append(svg("text",{x:t/duration*width,y:height-15,class:"ruler"},seconds(t)));
  const line=svg("g",{id:"record-playhead"});line.append(svg("line",{x1:0,x2:0,y1:18,y2:height-28,stroke:"var(--accent)","stroke-width":2}),svg("circle",{cx:0,cy:18,r:5,fill:"var(--accent)"}));node.append(line);player.emit();syncScrollbars();
};
document.fonts.ready.then(()=>renderRecording());

/* Clip canvases use only the memo's stored peaks; no audio decode or model call. */
let waveformFrame=null;
function scheduleClipWaves(){if(waveformFrame!==null)return;waveformFrame=requestAnimationFrame(()=>{waveformFrame=null;drawClipWaves();});}
function drawClipWaves(){
  const peaks=state.data?.peaks||[],duration=state.data?.duration_s||1;if(!peaks.length)return;
  for(const block of $("canvas-clips").querySelectorAll(".canvas-clip")){
    const canvas=block.querySelector("canvas"),take=state.takes.find(t=>t.id===block.dataset.waveTake),clip=state.canvas.clips[Number(block.dataset.waveIndex)];if(!canvas||!take||!clip)continue;
    const width=block.clientWidth,height=block.clientHeight,dpr=devicePixelRatio||1;canvas.width=Math.max(1,Math.round(width*dpr));canvas.height=Math.max(1,Math.round(height*dpr));const context=canvas.getContext("2d");context.scale(dpr,dpr);context.clearRect(0,0,width,height);
    const first=Math.max(0,Math.floor(take.start/duration*peaks.length)),end=Math.max(first+1,Math.ceil(take.end/duration*peaks.length)),part=peaks.slice(first,end),tokens=getComputedStyle(document.documentElement),text=tokens.getPropertyValue("--text").trim(),accent=tokens.getPropertyValue("--accent").trim(),played=playback.current?.context==="Canvas"?Math.max(0,Math.min(1,(playback.position-clip.start)/(take.end-take.start))):0;
    const count=Math.max(1,Math.floor(width/3));for(let i=0;i<count;i++){const start=Math.floor(i/count*part.length),finish=Math.max(start+1,Math.ceil((i+1)/count*part.length));let peak=0;for(let j=start;j<finish;j++)peak=Math.max(peak,part[j]||0);const h=Math.max(1,Math.min(height-12,peak*(height-12))),x=(i+.5)/count*width;context.fillStyle=i/count<played?accent:text;context.globalAlpha=i/count<played?1:.55;context.fillRect(x,(height-h)/2,Math.max(1,width/count*.55),h);}
    canvas.dataset.drawn="true";
  }
}
const previousCanvas=renderCanvas;renderCanvas=function(){previousCanvas();for(const[index,block]of[...$("canvas-clips").querySelectorAll(".canvas-clip")].entries()){
  const clip=state.canvas.clips[index],take=state.takes.find(t=>t.id===clip.take_id);if(!take)continue;
  for(const child of [...block.childNodes])if(child.nodeType===Node.TEXT_NODE){const label=el("span","clip-name-chip",child.textContent);block.replaceChild(label,child);}
  const wave=document.createElement("canvas");wave.className="clip-waveform";wave.setAttribute("aria-hidden","true");block.prepend(wave);block.dataset.waveTake=take.id;block.dataset.waveIndex=index;
}scheduleClipWaves();};
const previousPlayback=player.onChange;player.onChange=renderPlayback=function(value){previousPlayback(value);scheduleClipWaves();};
const previousTheme=theme;theme=function(value){previousTheme(value);scheduleClipWaves();};
syncAnalysis();renderCanvas();
