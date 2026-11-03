"use strict";
(function(root){
  const paths={
    play:'<path d="m9 5 11 7-11 7Z" fill="currentColor" stroke="none"/>',
    pause:'<path d="M7 5h4v14H7zM14 5h4v14h-4z" fill="currentColor" stroke="none"/>',
    'skip-back':'<path d="M5 5v14"/><path d="m19 5-10 7 10 7Z" fill="currentColor"/>',
    'skip-forward':'<path d="M19 5v14"/><path d="m5 5 10 7-10 7Z" fill="currentColor"/>',
    loop:'<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/>',
    download:'<path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4"/>',upload:'<path d="M12 16V3m-4 4 4-4 4 4M5 17v4h14v-4"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    moon:'<path d="M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',minus:'<path d="M5 12h14"/>',x:'<path d="m6 6 12 12M6 18 18 6"/>',
    'chevron-down':'<path d="m6 9 6 6 6-6"/>',
    grip:'<path d="M9 5h.01M15 5h.01M9 12h.01M15 12h.01M9 19h.01M15 19h.01"/>',
    'panel-left':'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    'more-horizontal':'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    stop:'<rect x="6" y="6" width="12" height="12" rx="1" fill="currentColor"/>'
  };
  function icon(name){const node=document.createElementNS('http://www.w3.org/2000/svg','svg');node.setAttribute('viewBox','0 0 24 24');node.setAttribute('fill','none');node.setAttribute('stroke','currentColor');node.setAttribute('stroke-width','1.75');node.setAttribute('stroke-linecap','round');node.setAttribute('stroke-linejoin','round');node.setAttribute('aria-hidden','true');node.classList.add('ui-icon');node.innerHTML=paths[name]||paths.play;return node;}
  function setIcon(node,name,label){node.replaceChildren(icon(name));if(label)node.append(document.createTextNode(label));}
  root.KeeperIcons={icon,setIcon};
})(window);
