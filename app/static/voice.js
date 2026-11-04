"use strict";
/* Deterministic copy is a rendering of observations, never a source of progress. */
(() => {
  const bank = {
    received: ["Got it. Pulling up a chair.", "{duration} of ideas. Let's hear it.", "Chair pulled up. Memo received.", "Your session's here. So am I."],
    loading_done: ["Listening to all {duration}. Yes, all of it.", "All {duration}, ready to listen.", "The whole room, loaded up.", "{duration} on the table. Let's get into it."],
    ast_start: ["Sorting singing from talking from whatever that noise is.", "Finding where the music lives.", "A little order for the room.", "Music here, talking there. Working through it."],
    ast_progress: ["Still listening. Nobody's rushing you.", "Making my way through the room.", "Another stretch listened to.", "Following the session, one window at a time."],
    spark_limit: ["The command budget is full. Keeping the rest as ideas.", "That's the command budget. The remaining music stays.", "The remaining spoken moments will stay unfiled.", "Keeping the memo, with the remaining commands left alone."],
    regions_found: ["Found {n_singing} stretches of music. Busy session.", "{n_speech} moments of you talking to yourself. Very normal.", "{n_singing} music stretches, with room to talk between them.", "The map's taking shape. {n_speech} spoken moments to check."],
    file: ['You said "{heard}". Filing it under {Section}.', '"{heard}". Noted. That\'s going in {Section}.', '"{heard}". {Section} it is.', 'Your words: "{heard}". Your folder: {Section}.'],
    keep: ['"{heard}". Say less. Keeping it.', "Keeper of the day: {time}.", '"{heard}". That one stays.', "Keeping the one you pointed out at {time}."],
    reject: ['"{heard}". Binned. No questions asked.', "Gone. Some takes are just warm-ups.", '"{heard}". Following your lead on that one.', "You called it. Setting that take aside."],
    none: ["Just thinking out loud. Not a command.", "Talking it through. Leaving that alone.", "A thought for the room. No filing needed.", "Letting that spoken thought be a thought."],
    performance: ["That wasn't talking. Counting it as music.", "That's performance. Keeping it with the music.", "Words in the music. Counting them with the performance.", "A sung thought. Back with the music it goes."],
    noise: ["Room noise. Leaving it out of the instructions.", "The room had a moment. No command there.", "That's background sound, according to the parser.", "No spoken instruction in that sound."],
    next: ["Called it before you sang it. Respect.", "A label for what comes next. Got it.", "Next one gets that instruction.", "Putting that note ahead of the music."],
    last_attempt: ["Only the last attempt. Got it.", "Just the last go, as requested.", "Last attempt only. Keeping the instruction local.", "That instruction belongs to the last attempt."],
    take_cut: ["Chopping {name}. Leaving a little air on each side.", "{name}, ready for its own row.", "One slice for {name}.", "Giving {name} a place on the map."],
    done: ["{n_takes} takes, {n_sections} sections. Your song's waiting.", "Done. Go make something.", "{n_takes} takes, all in their places.", "Session sorted into {n_sections} sections. Over to you."],
    failed: ["Something broke on my end, not yours: {message}", "I hit a snag: {message}", "The process stopped here: {message}", "Couldn't finish this session: {message}"]
  };
  function time(value) {
    const n = Math.max(0, Number(value) || 0);
    return `${Math.floor(n / 60)}:${String(Math.floor(n % 60)).padStart(2, "0")}`;
  }
  function hash(value) {
    let seed = 2166136261;
    for (const character of value) seed = Math.imul(seed ^ character.charCodeAt(0), 16777619);
    return seed >>> 0;
  }
  function personality(id, event, index, memory = {recent: [], last: new Map()}) {
    const d = event.data, section = d.section || "ideas";
    const values = {...d, heard: String(d.heard || "").replace(/[.,!?\s]+$/g, ""), duration: time(d.duration_s), time: time(d.start), Section: section.charAt(0).toUpperCase() + section.slice(1)};
    const chosen = [], recent = memory.recent.flat();
    const pick = key => {
      const choices = bank[key] || bank.none, offset = hash(`${id}:${index}:${key}`) % choices.length;
      const template = Array.from({length: choices.length}, (_, i) => choices[(offset + i) % choices.length])
        .find(candidate => !recent.includes(candidate) && memory.last.get(key) !== candidate);
      chosen.push(template); memory.last.set(key, template);
      return template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ""));
    };
    let line = pick(event.type === "spark_result" ? d.intent : event.type);
    if (event.type === "spark_result" && d.target === "next") line += " " + pick("next");
    if (event.type === "spark_result" && d.scope === "last_attempt") line += " " + pick("last_attempt");
    memory.recent.push(chosen); memory.recent = memory.recent.slice(-3);
    return line;
  }
  function createPersonality(id) {
    const memory = {recent: [], last: new Map()};
    return (event, index) => personality(id, event, index, memory);
  }
  function process(event) {
    const d = event.data, span = `${time(d.start)}–${time(d.end)}`;
    switch (event.type) {
      case "received": return `input   ${time(d.duration_s)}${d.sample_rate > 0 && d.channels > 0 ? ` · ${d.sample_rate} Hz ${d.channels === 1 ? "mono" : d.channels === 2 ? "stereo" : d.channels + " channels"}` : ""}`;
      case "loading_done": return `loaded  ${d.duration_s.toFixed(2)} s · mono float32`;
      case "ast_start": return `ast     ${d.n_windows} windows · ${d.window_s} s window · ${d.hop_s} s hop · ${d.device}${d.cached ? " · cached scores" : ""}`;
      case "ast_progress": return `ast     ${d.windows_done} windows scored`;
      case "spark_limit": return `spark   ${d.n_skipped} speech regions skipped · ${d.cap} call cap`;
      case "regions_found": return `regions ${d.n_singing} music · ${d.n_speech} speech · ${d.total_singing_s.toFixed(1)} s music`;
      case "spark_result": return `spark   ${span}  "${d.heard}"  → ${d.intent}${d.section ? " · " + d.section : ""} · ${d.target} · ${d.scope} · ${Number(d.confidence).toFixed(2)}${d.cached ? " · cached" : ""}`;
      case "take_cut": return `cut     ${d.name}  ${span}  (${(d.end - d.start).toFixed(1)} s)`;
      case "done": return `done    ${d.n_takes} takes · ${d.n_sections} sections · ${d.elapsed_s.toFixed(2)} s`;
      case "failed": return `failed  ${d.message}`;
      default: return event.type;
    }
  }
  globalThis.KeeperVoice = {personality, createPersonality, process, bank};
})();
