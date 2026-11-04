"use strict";
/* Public configuration supplies the same limits and availability as upload admission. */
(() => {
  let allowed = false;
  const button = $("upload"), input = $("file"), hint = $("upload-limits");
  button.disabled = true;
  const ready = json("/api/config").then(config => {
    allowed = config.uploads_enabled;
    hint.textContent = allowed ? `Up to ${config.max_audio_minutes} minutes and ${config.max_upload_mb} MB.`
      : "Uploads are paused. You can still listen to the showcase memo.";
    button.title = hint.textContent;
    button.setAttribute("aria-describedby", hint.id);
    $("browser-privacy").textContent = `Private to this browser. Deleted after ${config.retention_days} days.`;
    button.disabled = !allowed;
  }).catch(() => {
    hint.textContent = "Uploads are temporarily unavailable. Please refresh to try again.";
    button.title = hint.textContent;
  });
  const upload = input.onchange;
  input.onchange = async event => {
    await ready;
    if (!allowed) { input.value = ""; notice(hint.textContent, true); return; }
    try { await upload(event); } finally { if (!allowed) button.disabled = true; }
  };
})();
