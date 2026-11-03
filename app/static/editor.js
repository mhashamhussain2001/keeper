"use strict";
/* View interactions share the existing player, persistence, and owner-scoped API. */
const {icon,setIcon}=KeeperIcons;
const exportControl=el("button","icon");exportControl.id="transport-export";exportControl.setAttribute("aria-label","Export current audio");$("stop").before(exportControl);
exportControl.onclick=()=>attempt((async()=>{if(!playback.current?.clip_url||playback.current.context==="Canvas")return;const response=await api(playback.current.clip_url),blob=await response.blob(),url=URL.createObjectURL(blob),link=el("a");link.href=url;link.download=(playback.current.name||"Recording")+(blob.type.includes("mp4")?".m4a":".wav");link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);})());
const canonical=["intro","verse","pre-chorus","chorus","bridge","hook","outro","unsorted","unlabeled"];
function canEdit(){return !!state.data?.meta.owned&&!state.data.meta.showcase;}
function presentSections(){const keys=[...new Set(state.takes.map(t=>t.section))];return [...canonical.filter(k=>keys.includes(k)),...keys.filter(k=>!canonical.includes(k)&&k!=="rejected"),...(keys.includes("rejected")?["rejected"]:[])];}
const originalTitle=title;
function sectionTitle(key){return key==="pre-chorus"?"Pre-chorus":originalTitle(key);}
function setDisabled(node,disabled){node.disabled=!!disabled;node.setAttribute("aria-disabled",String(!!disabled));}
function paintIcons(){
  const playing=playback.mode==="playing",context=playback.current?.context;
  for(const[id,name,label]of[["theme",document.documentElement.dataset.theme==="dark"?"moon":"sun"],["sidebar-toggle","panel-left"],["upload","upload","Upload memo"],["record-play",context==="Recording"&&playing?"pause":"play"],["transport-play",playing?"pause":"play"],["prev","skip-back"],["next","skip-forward"],["loop","loop"],["stop","stop"],["transport-export","download"],["export","download","Export"],["play-through",context==="Canvas"&&playing?"pause":"play",context==="Canvas"&&playing?"Pause":"Play through"]]){
    const node=$(id);setIcon(node,name);if(label){const span=el("span","button-label",label);node.append(span);}
  }
  for(const button of document.querySelectorAll("[data-zoom]")){const action=button.dataset.zoom.split(":")[1];if(action!=="fit")setIcon(button,action==="+"?"plus":"minus");setDisabled(button,!state.data);}
  setDisabled($("record-play"),!state.data);setDisabled($("transport-play"),!state.data);
  setDisabled($("play-through"),!state.data||!state.canvas.clips.length);
  const active=!!playback.current&&["playing","paused"].includes(playback.mode);
  for(const id of["prev","next"])setDisabled($(id),!active);
  setDisabled($("loop"),!state.data);setDisabled($("stop"),!active);
  setDisabled(exportControl,!playback.current?.clip_url||context==="Canvas");
}

/* A single tooltip, dismissed by every interaction that could strand it. */
const tooltip=el("div","global-tooltip");tooltip.id="take-tooltip";tooltip.role="tooltip";tooltip.hidden=true;document.body.append(tooltip);
let tooltipRow=null;
function hideTooltip(){tooltip.hidden=true;tooltipRow=null;}
function showTooltip(row,take){if(!take.heard)return;tooltipRow=row;tooltip.replaceChildren(el("small","","FILED BECAUSE YOU SAID"),el("div","",`“${take.heard}”`));tooltip.hidden=false;const box=row.getBoundingClientRect(),size=tooltip.getBoundingClientRect();tooltip.style.left=`${Math.max(8,Math.min(innerWidth-size.width-8,box.right-size.width-15))}px`;tooltip.style.top=`${Math.max(8,Math.min(innerHeight-size.height-8,box.bottom-4))}px`;}
document.addEventListener("scroll",()=>{hideTooltip();if(menu)closeMenu();},{capture:true,passive:true});
document.addEventListener("pointerdown",event=>{if(!event.target.closest(".take-row")||event.pointerType!=="touch")hideTooltip();if(!event.target.closest(".editor-menu"))closeMenu();},true);
document.addEventListener("dragstart",hideTooltip,true);
document.addEventListener("keydown",event=>{if(event.key==="Escape"){hideTooltip();closeMenu(true);}});

/* Menus and text entry work with both pointer and keyboard input. */
let menu=null,menuAnchor=null,menuVersion=0;
function closeMenu(focus=false){menuVersion++;if(menu){menu.remove();menu=null;if(focus)menuAnchor?.focus();}}
function openMenu(anchor,items){
  closeMenu();hideTooltip();menuAnchor=anchor;menu=el("div","editor-menu");menu.role="menu";menu.setAttribute("aria-label","Actions");
  for(const item of items){const button=el("button","",item.label);button.role="menuitem";button.onclick=()=>{closeMenu();attempt(item.run());};menu.append(button);}
  const box=anchor.getBoundingClientRect();menu.style.left=`${Math.max(8,Math.min(innerWidth-208,box.left))}px`;menu.style.top=`${Math.min(innerHeight-items.length*32-12,box.bottom+4)}px`;document.body.append(menu);
  const size=menu.getBoundingClientRect();menu.style.top=`${Math.max(8,Math.min(box.bottom+4,innerHeight-size.height-8))}px`;
  menu.onkeydown=event=>{const buttons=[...menu.querySelectorAll("button")],at=buttons.indexOf(document.activeElement);if(["ArrowDown","ArrowUp","Home","End"].includes(event.key)){event.preventDefault();buttons[event.key==="Home"?0:event.key==="End"?buttons.length-1:(at+(event.key==="ArrowDown"?1:-1)+buttons.length)%buttons.length].focus();}if(event.key==="Escape"){event.preventDefault();event.stopPropagation();closeMenu(true);}};
  menu.querySelector("button")?.focus();
}
const nameDialog=document.createElement("dialog");nameDialog.id="name-dialog";nameDialog.setAttribute("aria-labelledby","name-dialog-title");nameDialog.innerHTML='<form method="dialog"><h2 id="name-dialog-title"></h2><input id="name-dialog-input" aria-label="Name" required maxlength="120"><div class="dialog-actions"><button value="cancel" formnovalidate>Cancel</button><button value="save" class="primary">Save</button></div></form>';document.body.append(nameDialog);
function askName(heading,value,callback,max=120){$("name-dialog-title").textContent=heading;const input=$("name-dialog-input");input.maxLength=max;input.value=value;nameDialog.onclose=()=>{if(nameDialog.returnValue==="save"&&input.value.trim())callback(input.value.trim());};nameDialog.returnValue="cancel";nameDialog.showModal();input.focus();input.select();}

/* Filing preserves the audio source and scroll position; Undo files back. */
const toast=el("div","move-toast");toast.id="move-toast";toast.setAttribute("role","status");toast.hidden=true;document.body.append(toast);let toastTimer;
async function refreshTakeData(){const data=await json(`/api/memos/${state.memo}`);state.data=data;state.takes=[];const keys=[...canonical,...Object.keys(data.songmap.sections).filter(k=>!canonical.includes(k))];for(const key of keys)for(const[index,take]of(data.songmap.sections[key]||[]).entries())state.takes.push({...take,name:take.custom_name||`${key==="unsorted"?"Idea":sectionTitle(key)} ${index+1}`});for(const queued of player.queue){const updated=state.takes.find(t=>t.id===(queued.take_id||queued.id));if(updated){queued.name=updated.name;queued.section=updated.section;}}renderTakes();renderRecording();renderCanvas();}
async function moveTake(take,target,undo=true){
  if(!canEdit())return;const old=take.section,scroll=$("take-list").scrollTop;
  await json(`/api/memos/${state.memo}/takes/${take.id}`,send("PATCH",{section:target}));await refreshTakeData();$("take-list").scrollTop=scroll;
  clearTimeout(toastTimer);toast.replaceChildren(el("span","",`Moved to ${sectionTitle(target)}`));if(undo){const button=el("button","","Undo");button.onclick=()=>attempt(moveTake(state.takes.find(t=>t.id===take.id),old,false));toast.append(button);}toast.hidden=false;toastTimer=setTimeout(()=>toast.hidden=true,6000);
}
function takeMenu(button,take){const targets=[...canonical,...presentSections().filter(key=>!canonical.includes(key)&&key!=="rejected")],items=[...targets.map(key=>({label:`Move to ${sectionTitle(key)}`,run:()=>moveTake(take,key)})),{label:"Rename take...",run:()=>askName("Rename take",take.name,async name=>{await json(`/api/memos/${state.memo}/takes/${take.id}`,send("PATCH",{name}));await refreshTakeData();})}];closeMenu();const version=menuVersion;requestAnimationFrame(()=>requestAnimationFrame(()=>{if(button.isConnected&&menuVersion===version)openMenu(button,items);}));}

renderTakes=function(){
  hideTooltip();const scroll=$("take-list").scrollTop;$("take-count").textContent=state.takes.filter(t=>t.section!=="rejected").length;$("take-count").classList.add("mono");$("filters").replaceChildren();
  for(const key of["all",...presentSections().filter(k=>k!=="rejected")]){const button=el("button",`chip ${state.filter===key?"active":""}`,key==="all"?"All":sectionTitle(key));button.setAttribute("aria-pressed",String(state.filter===key));button.onclick=()=>{state.filter=key;renderTakes();};$("filters").append(button);}
  $("take-list").replaceChildren();for(const section of presentSections()){
    const takes=state.takes.filter(t=>t.section===section&&(state.filter==="all"||section===state.filter));if(!takes.length)continue;
    const group=el(section==="rejected"?"details":"section","take-group");group.dataset.group=section;const header=el(section==="rejected"?"summary":"div","group-title",section==="rejected"?`Rejected (${takes.length})`:sectionTitle(section));group.append(header);$("take-list").append(group);
    group.ondragover=event=>{if(!canEdit()||!event.dataTransfer.types.includes("take"))return;event.preventDefault();event.stopPropagation();group.classList.add("drop-target");};group.ondragleave=event=>{if(!group.contains(event.relatedTarget))group.classList.remove("drop-target");};group.ondrop=event=>{event.preventDefault();event.stopPropagation();group.classList.remove("drop-target");if(!canEdit())return;const take=state.takes.find(t=>t.id===event.dataTransfer.getData("take"));if(take&&section!=="rejected")attempt(moveTake(take,section));};
    for(const take of takes){
      const row=el("div","take-row");row.dataset.take=take.id;row.dataset.name=take.name;row.dataset.section=section;row.style.setProperty("--section",color(section));row.tabIndex=0;
      const handle=el("span","handle");handle.append(icon("grip"));handle.setAttribute("aria-label",`Drag ${take.name}`);handle.draggable=!!canEdit();
      // Native desktop drag remains available on the row; the visible grip identifies its purpose.
      row.draggable=!!canEdit();row.ondragstart=event=>{if(!canEdit()){event.preventDefault();return;}hideTooltip();event.dataTransfer.setData("take",take.id);};
      row.append(handle,el("span","take-bar"));const info=el("div","take-info");info.append(el("div","take-name",take.name),el("div","take-progress"));row.append(info,el("span","mono",seconds(take.end-take.start)));
      if(canEdit()){const more=el("button","take-more circle");setIcon(more,"more-horizontal");more.setAttribute("aria-label",`Actions for ${take.name}`);more.setAttribute("aria-haspopup","menu");more.onclick=()=>takeMenu(more,take);row.append(more);}
      const play=el("button","circle play-take");setIcon(play,"play");play.setAttribute("aria-label",`Play ${take.name}`);setDisabled(play,!take.clip_url);play.onclick=()=>{hideTooltip();playTake(take);};row.append(play);group.append(row);
      row.onpointerenter=event=>{if(event.pointerType!=="touch")showTooltip(row,take);};row.onmouseenter=()=>showTooltip(row,take);row.onpointerleave=hideTooltip;row.onmouseleave=hideTooltip;row.onfocusin=event=>{if(!event.target.closest("button"))showTooltip(row,take);};row.onfocusout=hideTooltip;row.onpointerup=event=>{if(event.pointerType==="touch"&&!event.target.closest("button,.handle")){if(tooltipRow===row)hideTooltip();else showTooltip(row,take);}};
    }
  }$("take-list").scrollTop=scroll;player.emit();syncScrollbars();
};

/* One full-height, scale-aligned grid layer under all drawing content. */
const baseCanvas=renderCanvas;
function renameSection(section){askName("Rename section",section.name,name=>{section.name=name;renderCanvas();saveCanvas();},80);}
function reorderSection(index,direction){const to=index+direction;if(to<0||to>=state.canvas.sections.length)return;const[section]=state.canvas.sections.splice(index,1);state.canvas.sections.splice(to,0,section);let cursor=0;for(const item of state.canvas.sections){const duration=item.end-item.start;item.start=cursor;item.end=cursor+duration;cursor=item.end;}renderCanvas();saveCanvas();}
const sectionTypes=["intro","verse","pre-chorus","chorus","bridge","hook","outro"];
function typeItems(callback){return[...sectionTypes.map(type=>({label:sectionTitle(type),run:()=>callback(type,sectionTitle(type))})),{label:"Custom...",run:()=>askName("Custom section","",name=>callback("custom",name),80)}];}
function sectionMenu(button,index){const section=state.canvas.sections[index];openMenu(button,[...typeItems((type,name)=>{section.type=type;section.name=name;renderCanvas();saveCanvas();}),{label:"Rename...",run:()=>renameSection(section)},{label:"Move left",run:()=>reorderSection(index,-1)},{label:"Move right",run:()=>reorderSection(index,1)},{label:"Delete",run:()=>{state.canvas.sections.splice(index,1);renderCanvas();saveCanvas();}}]);}
renderCanvas=function(){
  baseCanvas();const scale=canvasScale(),board=$("canvas-board");board.style.setProperty("--grid-step",`${scale*5}px`);board.style.setProperty("--major-step",`${scale*15}px`);
  for(const[index,block]of[...$("canvas-sections").querySelectorAll(".canvas-section")].entries()){
    const section=state.canvas.sections[index];block.draggable=canEdit();block.querySelector(".section-resize").addEventListener("pointerdown",event=>{if(!canEdit()){event.preventDefault();event.stopImmediatePropagation();}},true);block.style.setProperty("--section",color(section.type||section.name.toLowerCase()));block.tabIndex=0;block.setAttribute("role","button");block.setAttribute("aria-label",`Section ${section.name}`);block.setAttribute("aria-haspopup","menu");const chevron=icon("chevron-down");chevron.classList.add("section-chevron");block.append(chevron);
    let timer;block.onclick=event=>{if(!canEdit()||event.target.closest(".section-resize")||event.detail>1)return;timer=setTimeout(()=>sectionMenu(block,index),180);};block.ondblclick=()=>{clearTimeout(timer);closeMenu();if(canEdit())renameSection(section);};block.onkeydown=event=>{if(["Enter"," ","ArrowDown"].includes(event.key)&&canEdit()){event.preventDefault();sectionMenu(block,index);}};
  }
  const add=$("add-section");setDisabled(add,!canEdit());setIcon(add,"plus","Section");add.setAttribute("aria-haspopup","menu");add.onclick=()=>openMenu(add,typeItems((type,name)=>{const start=Math.max(0,...state.canvas.sections.map(s=>s.end));state.canvas.sections.push({name,type,start,end:start+16});renderCanvas();saveCanvas();}));
  for(const[index,block]of[...$("canvas-clips").querySelectorAll(".canvas-clip")].entries()){const clip=state.canvas.clips[index],remove=block.querySelector(".remove-clip");remove.hidden=!canEdit();block.addEventListener("pointerdown",event=>{if(!canEdit()){event.preventDefault();event.stopImmediatePropagation();}},true);setIcon(remove,"x");block.ondblclick=()=>playCanvas(clip.start);}
  syncScrollbars();paintIcons();
};

/* Contextual transport and a single shared Canvas playhead. */
function playCanvas(offset=0){hideTooltip();const queue=canvasQueue(state.canvas,state.takes);if(queue.length)attempt(player.play(queue,offset));}
$("canvas-ruler").onclick=event=>{if(!state.data)return;playCanvas(Math.max(0,(event.clientX-$("canvas-board").getBoundingClientRect().left)/canvasScale()));};
$("prev").onclick=()=>navigate(-1);$("next").onclick=()=>navigate(1);
function navigate(direction){
  hideTooltip();if(!playback.current||!["playing","paused"].includes(player.mode))return;
  if(playback.current.context==="Recording"){attempt(player.seek(playback.position+direction*10));return;}
  if(playback.current.context==="Canvas"){
    const ordered=state.canvas.clips.map((clip,index)=>({...clip,index})).sort((a,b)=>a.start-b.start||a.lane-b.lane);
    const match=/^canvas-(\d+)-/.exec(playback.current.id),index=match?ordered.findIndex(c=>c.index===Number(match[1])):ordered.findIndex(c=>c.start>=playback.position);
    const clip=ordered[Math.max(0,Math.min(ordered.length-1,index+direction))];if(clip)playCanvas(clip.start);return;
  }
  const takes=state.takes.filter(t=>(state.filter==="all"||t.section===state.filter)&&t.section!=="rejected"&&t.clip_url),index=takes.findIndex(t=>t.id===(playback.current.take_id||playback.current.id));
  const take=takes[index<0?0:(index+direction+takes.length)%takes.length];if(take)playTake(take);
}
const basePlayback=renderPlayback;
player.onChange=renderPlayback=function(value){
  basePlayback(value);paintIcons();
  const current=value.current,canvas=current?.context==="Canvas",head=$("canvas-playhead");head.hidden=!canvas;
  if(canvas){const x=value.position*canvasScale();head.style.left=`${x}px`;const scroller=$("canvas-scroll");if(value.mode==="playing"&&(x<scroller.scrollLeft+12||x>scroller.scrollLeft+scroller.clientWidth-20))scroller.scrollLeft=Math.max(0,x-scroller.clientWidth*.3);}
  const index=player.queue.indexOf(current),before=player.lengths.slice(0,Math.max(0,index)).reduce((a,b)=>a+b,0),fraction=(value.position-before)/Math.max(.01,player.lengths[index]||value.total);
  for(const row of document.querySelectorAll(".take-row")){const active=(current?.take_id||current?.id)===row.dataset.take&&current?.context!=="Recording",button=row.querySelector(".play-take");setIcon(button,active&&value.mode==="playing"?"pause":"play");row.querySelector(".take-progress").style.width=active?`${Math.max(0,Math.min(1,fraction))*100}%`:"0";}
};
const basePlayTake=playTake;playTake=function(take){hideTooltip();if(player.current?.context==="Canvas"&&player.current.take_id===take.id&&["playing","paused"].includes(player.mode))attempt(player.toggle());else basePlayTake(take);};
const baseLoad=loadMemo;loadMemo=async function(id){hideTooltip();closeMenu();await baseLoad(id);$("memo-meta").classList.remove("empty-subtitle");paintIcons();syncScrollbars();};
const baseEmpty=emptyMemo;emptyMemo=function(){baseEmpty();$("memo-meta").classList.add("empty-subtitle");paintIcons();};

/* Cursor-anchored zoom and one scrollbar per view, sharing native scroll position. */
function zoomView(type,factor,clientX,fit=false){
  if(!state.data)return;const scroller=$(type==="record"?"record-scroll":"canvas-scroll"),box=scroller.getBoundingClientRect(),anchor=Math.max(0,Math.min(scroller.clientWidth,(clientX??box.left+scroller.clientWidth/2)-box.left)),old=state[`${type}Zoom`],position=(scroller.scrollLeft+anchor)/old;
  state[`${type}Zoom`]=fit?1:Math.max(1,Math.min(16,old*factor));type==="record"?renderRecording():renderCanvas();scroller.scrollLeft=Math.max(0,position*state[`${type}Zoom`]-anchor);syncScrollbars();
}
for(const button of document.querySelectorAll("[data-zoom]"))button.onclick=()=>{const[type,action]=button.dataset.zoom.split(":");zoomView(type,action==="+"?1.4:1/1.4,undefined,action==="fit");};
for(const[type,id]of[["record","record-scroll"],["canvas","canvas-scroll"]]){
  const scroller=$(id);scroller.addEventListener("wheel",event=>{hideTooltip();closeMenu();if(event.ctrlKey||event.metaKey){event.preventDefault();event.stopImmediatePropagation();zoomView(type,Math.exp(-event.deltaY*.01),event.clientX);}else if(scroller.scrollWidth>scroller.clientWidth){event.preventDefault();event.stopImmediatePropagation();scroller.scrollLeft+=Math.abs(event.deltaX)>Math.abs(event.deltaY)?event.deltaX:event.deltaY;syncScrollbars();}},{passive:false,capture:true});
}
const scrollbars=[];
function addScrollbar(scroller,vertical=false){
  const rail=el("div",`custom-scrollbar ${vertical?"vertical":""}`),thumb=el("div","scroll-thumb");rail.tabIndex=0;rail.role="scrollbar";rail.setAttribute("aria-label",vertical?"Scroll takes":"Scroll "+(scroller.id==="record-scroll"?"recording":"canvas"));rail.setAttribute("aria-controls",scroller.id);rail.setAttribute("aria-orientation",vertical?"vertical":"horizontal");rail.setAttribute("aria-valuemin","0");rail.append(thumb);scroller.after(rail);scrollbars.push({scroller,rail,thumb,vertical});
  const move=value=>{scroller[vertical?"scrollTop":"scrollLeft"]=value;syncScrollbars();};
  rail.onkeydown=event=>{const axis=vertical?"scrollTop":"scrollLeft",range=vertical?scroller.scrollHeight-scroller.clientHeight:scroller.scrollWidth-scroller.clientWidth;if(["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End","PageDown","PageUp"].includes(event.key)){event.preventDefault();move(event.key==="Home"?0:event.key==="End"?range:scroller[axis]+(["ArrowLeft","ArrowUp","PageUp"].includes(event.key)?-1:1)*(event.key.startsWith("Page")?200:40));}};
  rail.onpointerdown=event=>{event.preventDefault();rail.focus();rail.setPointerCapture(event.pointerId);rail.classList.add("dragging");const bounds=rail.getBoundingClientRect(),start=vertical?bounds.top:bounds.left,size=vertical?bounds.height:bounds.width,thumbSize=vertical?thumb.offsetHeight:thumb.offsetWidth,range=vertical?scroller.scrollHeight-scroller.clientHeight:scroller.scrollWidth-scroller.clientWidth;const shift=event.target===thumb?(vertical?event.clientY:event.clientX)-(vertical?thumb.getBoundingClientRect().top:thumb.getBoundingClientRect().left):thumbSize/2;const drag=next=>move(Math.max(0,Math.min(range,((vertical?next.clientY:next.clientX)-start-shift)/Math.max(1,size-thumbSize)*range)));drag(event);rail.onpointermove=drag;rail.onpointerup=()=>{rail.onpointermove=null;rail.classList.remove("dragging");};};
  scroller.addEventListener("scroll",syncScrollbars,{passive:true});
}
function syncScrollbars(){for(const{scroller,rail,thumb,vertical}of scrollbars){if(vertical)rail.style.top=(scroller.getBoundingClientRect().top-scroller.parentElement.getBoundingClientRect().top)+"px";const size=vertical?scroller.clientHeight:scroller.clientWidth,total=vertical?scroller.scrollHeight:scroller.scrollWidth,position=vertical?scroller.scrollTop:scroller.scrollLeft,range=Math.max(0,total-size),ratio=Math.min(1,size/Math.max(1,total)),track=vertical?rail.clientHeight:rail.clientWidth,thumbSize=Math.max(20,track*ratio);thumb.style[vertical?"height":"width"]=`${Math.min(track,thumbSize)}px`;thumb.style.transform=`translate${vertical?"Y":"X"}(${range?position/range*(track-thumbSize):0}px)`;rail.setAttribute("aria-valuemax",String(range));rail.setAttribute("aria-valuenow",String(position));rail.setAttribute("aria-disabled",String(!range));}}
addScrollbar($("record-scroll"));addScrollbar($("canvas-scroll"));addScrollbar($("take-list"),true);

/* Sidebar width changes refit both timelines without changing their time scale. */
function collapseSidebar(collapsed){document.documentElement.classList.toggle("sidebar-collapsed",collapsed);$("sidebar-toggle").setAttribute("aria-expanded",String(!collapsed));try{localStorage.setItem("keeper-sidebar-collapsed",String(collapsed));}catch(_){}hideTooltip();closeMenu();}
try{collapseSidebar(localStorage.getItem("keeper-sidebar-collapsed")==="true");}catch(_){}
$("sidebar-toggle").onclick=()=>collapseSidebar(!document.documentElement.classList.contains("sidebar-collapsed"));
document.addEventListener("keydown",event=>{if(event.key==="["&&!event.target.closest('input,textarea,select,[contenteditable=true]')&&!document.querySelector("dialog[open]")){event.preventDefault();$("sidebar-toggle").click();}});
let resizeFrame;new ResizeObserver(()=>{cancelAnimationFrame(resizeFrame);resizeFrame=requestAnimationFrame(()=>{renderRecording();renderCanvas();syncScrollbars();});}).observe(document.querySelector("main"));
const baseDrop=dropTake;dropTake=function(...args){if(canEdit())baseDrop(...args);};
paintIcons();renderTakes();renderCanvas();
