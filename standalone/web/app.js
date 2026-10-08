const state = {
  players: [],
  queues: [],
  selectedPlayerId: localStorage.getItem("family-music-player-id") || null,
  view: "now",
  provider: "all",
  pendingUri: null,
  pollTimer: null,
  pendingMute: null,
  pendingVolume: null,
  seeking: false,
  destinationOpen: false,
  balanceOpen: false,
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.error) {
    throw new Error(data?.error || `Request failed (${response.status})`);
  }
  return data;
}

function selectedPlayer() {
  return state.players.find((player) => player.player_id === state.selectedPlayerId) || null;
}

function selectedQueue() {
  const player = selectedPlayer();
  if (!player) return null;
  return (
    state.queues.find((queue) => queue.queue_id === player.player_id) ||
    state.queues.find((queue) => queue.queue_id === player.active_source) ||
    null
  );
}

function queueId() {
  return selectedQueue()?.queue_id || selectedPlayer()?.player_id || null;
}

function playerName(player) {
  return player?.name || player?.display_name || player?.player_id || "Speaker";
}

function mediaName(item) {
  const media = item?.media_item || item || {};
  return media.name || media.title || item?.name || "Nothing playing";
}

function mediaArtist(item) {
  const media = item?.media_item || item || {};
  const artists = media.artists;
  if (Array.isArray(artists)) {
    const names = artists.map((artist) => artist?.name).filter(Boolean);
    if (names.length) return names.join(", ");
  }
  return media.artist || media.artist_name || media.album?.name || "";
}

function mediaImage(item) {
  const media = item?.media_item || item || {};
  if (typeof media.image === "string") return media.image;
  if (typeof media.image_url === "string") return media.image_url;
  const images = media.metadata?.images;
  if (Array.isArray(images)) {
    const match = images.find((image) => typeof image?.path === "string");
    if (match) return match.path;
  }
  return "";
}

function showToast(message, timeout = 1600) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), timeout);
}

function setView(view) {
  state.view = view;
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  document.querySelectorAll(".tabbar button").forEach((node) => node.classList.remove("active"));
  $(`#${view}View`)?.classList.add("active");
  document.querySelector(`.tabbar button[data-view="${view}"]`)?.classList.add("active");
  if (view === "favorites") loadFavorites();
  if (view === "recents") loadRecents();
}

function renderPlayerSelect() {
  if (!state.players.length) return;
  if (!state.selectedPlayerId || !state.players.some((p) => p.player_id === state.selectedPlayerId)) {
    state.selectedPlayerId = state.players[0].player_id;
    localStorage.setItem("family-music-player-id", state.selectedPlayerId);
  }
}

function destinationIsPlaying(player) {
  if (!player) return false;
  if (player.state === "playing") return true;
  return state.queues.some(
    (queue) =>
      (queue.queue_id === player.player_id || queue.queue_id === player.active_source) &&
      queue.state === "playing"
  );
}

function destinationActivityMarkup(player, selected = false) {
  if (destinationIsPlaying(player)) {
    return '<span class="playing-equalizer" title="Playing" aria-label="Playing"><i></i><i></i><i></i></span>';
  }
  return selected
    ? '<span class="destination-selected" aria-label="Selected">●</span>'
    : '<span class="destination-activity" aria-hidden="true"></span>';
}

function updateDestinationActivity() {
  if (!state.destinationOpen) return;
  document.querySelectorAll(".destination-option[data-player]").forEach((button) => {
    const player = state.players.find((item) => item.player_id === button.dataset.player);
    const slot = button.querySelector(".destination-status");
    if (!slot || !player) return;
    slot.innerHTML = destinationActivityMarkup(player, player.player_id === state.selectedPlayerId);
  });
}

function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function currentPosition(queue) {
  let position = Number(queue?.elapsed_time) || 0;
  const updated = Number(queue?.elapsed_time_last_updated);
  if (queue?.state === "playing" && Number.isFinite(updated)) position += Math.max(0, Date.now() / 1000 - updated);
  return position;
}

function effectiveVolume(player) {
  const pending = state.pendingVolume?.playerId === player?.player_id ? state.pendingVolume : null;
  if (pending && Date.now() < pending.expiresAt) return pending.level;
  const raw = player?.type === "group" ? player?.group_volume : player?.volume_level;
  return Number.isFinite(Number(raw)) ? Math.round(Number(raw)) : 0;
}

function renderNow() {
  const player = selectedPlayer();
  const queue = selectedQueue();
  // Music Assistant may retain the last queue item after a Sonos-native
  // station/source change. In that case the player media is authoritative.
  const queueActive = queue?.state === "playing" || queue?.state === "paused";
  const item = queueActive
    ? (queue?.current_item || player?.current_media || null)
    : (player?.current_media || queue?.current_item || null);
  $("#trackTitle").textContent = mediaName(item);
  $("#trackMeta").textContent = mediaArtist(item) || "—";
  $("#destinationName").textContent = playerName(player);

  const artwork = $("#artwork");
  const image = mediaImage(item);
  artwork.innerHTML = image ? `<img src="${escapeHtml(image)}" alt="">` : "<span>♪</span>";

  const playing = player?.state === "playing" || (queueActive && queue?.state === "playing");
  $("#playPause").textContent = playing ? "❚❚" : "▶";

  const pendingMute = state.pendingMute?.playerId === player?.player_id ? state.pendingMute : null;
  const muted = pendingMute ? pendingMute.muted : Boolean(player?.volume_muted);
  $("#mute").textContent = muted ? "🔇" : "🔊";
  $("#mute").setAttribute("aria-pressed", String(muted));

  const volume = effectiveVolume(player);
  if (!state.seeking && document.activeElement !== $("#volume")) $("#volume").value = volume;
  $("#volumeValue").textContent = volume;

  const duration = Number(item?.duration || item?.media_item?.duration || 0);
  const position = Math.min(duration || Infinity, queueActive ? currentPosition(queue) : Number(item?.elapsed_time || 0));
  if (!state.seeking && document.activeElement !== $("#progress")) $("#progress").value = duration > 0 ? position / duration * 100 : 0;
  $("#elapsed").textContent = formatTime(position);
  $("#remaining").textContent = `-${formatTime(Math.max(0, duration - position))}`;

  $("#shuffle").classList.toggle("active", Boolean(queue?.shuffle_enabled));
  $("#repeat").classList.toggle("active", queue?.repeat_mode && queue.repeat_mode !== "off");
  $("#repeat").textContent = queue?.repeat_mode === "one" ? "↔¹" : "↔";
}

async function refreshState() {
  try {
    const result = await api("/api/state");
    state.players = Array.isArray(result.players) ? result.players.filter((p) => p.available !== false) : [];
    state.queues = Array.isArray(result.queues) ? result.queues : [];
    if (state.pendingMute) {
      const acknowledged = state.players.find((p) => p.player_id === state.pendingMute.playerId);
      if (
        (acknowledged && Boolean(acknowledged.volume_muted) === state.pendingMute.muted) ||
        Date.now() >= state.pendingMute.expiresAt
      ) {
        state.pendingMute = null;
      }
    }
    renderPlayerSelect();
    if (state.pendingVolume) {
      const p = state.players.find((x) => x.player_id === state.pendingVolume.playerId);
      const actual = p?.type === "group" ? p?.group_volume : p?.volume_level;
      if (Number(actual) === state.pendingVolume.level || Date.now() >= state.pendingVolume.expiresAt) state.pendingVolume = null;
    }
    renderNow();
    updateDestinationActivity();
  } catch (error) {
    showToast(error.message, 3000);
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function itemSubtitle(item) {
  const artists = Array.isArray(item?.artists)
    ? item.artists.map((artist) => artist?.name).filter(Boolean).join(", ")
    : "";
  return artists || item?.album?.name || item?.media_type || "";
}

function itemMarkup(item, row = false) {
  const uri = escapeHtml(item?.uri || "");
  const name = escapeHtml(item?.name || "Music");
  const subtitle = escapeHtml(itemSubtitle(item));
  const image = item?.image
    ? `<img src="${escapeHtml(item.image)}" alt="">`
    : "<span>♪</span>";
  if (row) {
    return `<button class="recent-item playable" data-uri="${uri}">
      <div class="thumb">${image}</div>
      <div><div class="title">${name}</div><div class="sub">${subtitle}</div></div>
    </button>`;
  }
  return `<button class="media-item playable" data-uri="${uri}">
    <div class="thumb">${image}</div>
    <div class="title">${name}</div>
    <div class="sub">${subtitle}</div>
  </button>`;
}

function findItem(uri, collections) {
  for (const collection of collections) {
    const match = (collection || []).find((item) => item?.uri === uri);
    if (match) return match;
  }
  return null;
}

function wirePlayable(container, collections) {
  container.querySelectorAll(".playable").forEach((button) => {
    button.addEventListener("click", async () => {
      const item = findItem(button.dataset.uri, collections);
      if (item) await playItem(item, button);
    });
  });
}

function markStarting(button) {
  document.querySelectorAll(".starting").forEach((node) => node.classList.remove("starting"));
  document.querySelectorAll(".feedback").forEach((node) => node.remove());
  button.classList.add("starting");
  button.setAttribute("aria-busy", "true");
  const feedback = document.createElement("span");
  feedback.className = "feedback";
  feedback.innerHTML = '<span class="spinner"></span><span>Starting…</span>';
  button.appendChild(feedback);
}

async function playItem(item, button) {
  const qid = queueId();
  if (!qid || !item?.uri) return;
  state.pendingUri = item.uri;
  markStarting(button);
  try {
    await api("/api/play", {
      method: "POST",
      body: JSON.stringify({queue_id: qid, media: item.uri}),
    });
    showToast(`Starting ${item.name || "music"}`);
    setView("now");
    await refreshState();
  } catch (error) {
    showToast(error.message, 3000);
    button.classList.remove("starting");
    button.removeAttribute("aria-busy");
    button.querySelector(".feedback")?.remove();
  } finally {
    state.pendingUri = null;
  }
}

function renderSections(target, data) {
  const sections = [
    ["Sonos Favorites", data.sonos_favorites],
    ["Artists", data.artists],
    ["Albums", data.albums],
    ["Tracks", data.tracks],
    ["Playlists", data.playlists],
    ["Radio", data.radio],
  ].filter(([, items]) => Array.isArray(items) && items.length);
  if (!sections.length) {
    target.innerHTML = '<div class="empty">Nothing here yet.</div>';
    return;
  }
  target.innerHTML = sections
    .map(([title, items]) => `<section class="section"><h3>${title}</h3><div class="grid">${items.map((item) => itemMarkup(item)).join("")}</div></section>`)
    .join("");
  wirePlayable(target, sections.map(([, items]) => items));
}

async function loadFavorites() {
  const body = $("#favoritesBody");
  body.innerHTML = '<div class="empty">Loading favorites…</div>';
  try {
    const data = await api("/api/favorites");
    renderSections(body, data);
  } catch (error) {
    body.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function loadRecents() {
  const body = $("#recentsBody");
  const qid = queueId();
  if (!qid) {
    body.innerHTML = '<div class="empty">Choose a speaker first.</div>';
    return;
  }
  body.innerHTML = '<div class="empty">Loading recents…</div>';
  try {
    const items = await api(`/api/recents?queue_id=${encodeURIComponent(qid)}`);
    body.innerHTML = items.length
      ? `<section class="section"><h3>Recently Played</h3><div class="recent-list">${items.map((item) => itemMarkup(item, true)).join("")}</div></section>`
      : '<div class="empty">Nothing played recently.</div>';
    wirePlayable(body, [items]);
  } catch (error) {
    body.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function runSearch() {
  const query = $("#searchInput").value.trim();
  if (!query) return;
  const body = $("#searchBody");
  body.innerHTML = '<div class="empty">Searching…</div>';
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}&provider=${encodeURIComponent(state.provider)}`);
    renderSections(body, data);
  } catch (error) {
    body.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function transport(action, button) {
  const qid = queueId();
  if (!qid) return;
  button.classList.add("accepted");
  window.setTimeout(() => button.classList.remove("accepted"), 450);
  try {
    await api("/api/transport", {
      method: "POST",
      body: JSON.stringify({queue_id: qid, action}),
    });
    await refreshState();
  } catch (error) {
    showToast(error.message, 3000);
  }
}

let volumeTimer = null;
function setVolume(level, flush = false) {
  const player = selectedPlayer();
  if (!player) return;
  const bounded = Math.max(0, Math.min(100, Math.round(Number(level))));
  state.pendingVolume = {playerId: player.player_id, level: bounded, expiresAt: Date.now() + 5000};
  $("#volume").value = bounded;
  $("#volumeValue").textContent = bounded;
  const send = async () => {
    volumeTimer = null;
    try {
      await api("/api/volume", {method:"POST", body:JSON.stringify({player_id:player.player_id, level:bounded})});
    } catch (error) { state.pendingVolume = null; showToast(error.message, 3000); }
  };
  clearTimeout(volumeTimer);
  if (flush) send(); else volumeTimer = window.setTimeout(send, 45);
}

async function toggleMute() {
  const player = selectedPlayer();
  if (!player) return;
  const currentMuted =
    state.pendingMute?.playerId === player.player_id
      ? state.pendingMute.muted
      : Boolean(player.volume_muted);
  const muted = !currentMuted;
  state.pendingMute = {playerId: player.player_id, muted, expiresAt: Date.now() + 5000};
  renderNow();
  try {
    await api("/api/mute", {
      method: "POST",
      body: JSON.stringify({player_id: player.player_id, muted}),
    });
  } catch (error) {
    state.pendingMute = null;
    renderNow();
    showToast(error.message, 3000);
  }
}

async function queueControl(action, value) {
  const qid = queueId();
  if (!qid) return;
  try {
    await api("/api/queue-control", {method:"POST", body:JSON.stringify({queue_id:qid, action, value})});
    await refreshState();
  } catch (error) { showToast(error.message, 3000); }
}

function toggleShuffle() {
  queueControl("shuffle", !Boolean(selectedQueue()?.shuffle_enabled));
}

function toggleRepeat() {
  const current = selectedQueue()?.repeat_mode || "off";
  const next = current === "off" ? "all" : current === "all" ? "one" : "off";
  queueControl("repeat", next);
}

function seekTo(percent) {
  const queue = selectedQueue();
  const item = queue?.current_item;
  const duration = Number(item?.duration || item?.media_item?.duration || 0);
  if (!duration) return;
  queueControl("seek", Math.round(duration * Number(percent) / 100));
}

function nudgeVolume(delta) {
  setVolume(effectiveVolume(selectedPlayer()) + delta, true);
}

function closeOverlay() {
  state.destinationOpen = false;
  state.balanceOpen = false;
  $("#overlay").classList.remove("open");
  $("#overlay").innerHTML = "";
  $(".tabbar")?.classList.remove("overlay-hidden");
}

function renderDestinationOverlay() {
  state.destinationOpen = true; state.balanceOpen = false;
  const options = state.players.map((p) => {
    const selected = p.player_id === state.selectedPlayerId;
    return `<button class="destination-option ${selected?"selected":""}" data-player="${escapeHtml(p.player_id)}"><span>${p.type==="group"?"▣":"◉"}</span><span>${escapeHtml(playerName(p))}</span><span class="destination-status">${destinationActivityMarkup(p, selected)}</span></button>`;
  }).join("");
  $("#overlay").classList.add("open");
  $(".tabbar")?.classList.add("overlay-hidden");
  $("#overlay").innerHTML = `<button class="scrim" aria-label="Close"></button><div class="popover"><h3>Play in</h3>${options}</div>`;
  $(".scrim").addEventListener("click", closeOverlay);
  document.querySelectorAll("[data-player]").forEach((button) => button.addEventListener("click", () => {
    state.selectedPlayerId = button.dataset.player;
    localStorage.setItem("family-music-player-id", state.selectedPlayerId);
    closeOverlay(); renderNow();
  }));
}

function renderBalanceOverlay() {
  const player = selectedPlayer();
  state.balanceOpen = true; state.destinationOpen = false;
  const ids = Array.isArray(player?.group_childs) ? player.group_childs : [];
  const members = ids.map((id) => state.players.find((p) => p.player_id === id)).filter(Boolean);
  let body = '<div class="balance-help">This destination is a single speaker.</div>';
  if (members.length) {
    const avg = Math.round(members.reduce((s,p)=>s+effectiveVolume(p),0)/members.length);
    body = '<div class="balance-help">Shape where the sound comes from. No percentages to manage.</div>' +
      members.map((p) => {
        const trim = Math.max(-12, Math.min(12, Math.round((effectiveVolume(p)-avg)/2)));
        return `<div class="balance-row"><div class="balance-name">${escapeHtml(playerName(p))}</div><input class="member-trim" data-member="${escapeHtml(p.player_id)}" data-base="${avg}" type="range" min="-12" max="12" step="1" value="${trim}"></div>`;
      }).join("") +
      '<div class="balance-scale"><span>Less</span><span>Neutral</span><span>More</span></div><button id="resetBalance" class="reset-balance">Reset balance</button>';
  }
  $("#overlay").classList.add("open");
  $(".tabbar")?.classList.add("overlay-hidden");
  $("#overlay").innerHTML = `<button class="scrim" aria-label="Close"></button><div class="popover"><h3>Speaker Balance</h3>${body}</div>`;
  $(".scrim").addEventListener("click", closeOverlay);
  document.querySelectorAll(".member-trim").forEach((slider) => {
    const apply = (flush) => {
      const target = Math.max(0,Math.min(100,Number(slider.dataset.base)+Number(slider.value)*2));
      const p = state.players.find((x)=>x.player_id===slider.dataset.member);
      if (p) {
        state.pendingVolume={playerId:p.player_id,level:target,expiresAt:Date.now()+5000};
        api("/api/volume",{method:"POST",body:JSON.stringify({player_id:p.player_id,level:target})}).catch((e)=>showToast(e.message,3000));
      }
    };
    slider.addEventListener("input",()=>apply(false)); slider.addEventListener("change",()=>apply(true));
  });
  $("#resetBalance")?.addEventListener("click",()=>document.querySelectorAll(".member-trim").forEach((slider)=>{slider.value=0;slider.dispatchEvent(new Event("change"));}));
}

let voiceRecognition = null;

function startVoiceSearch() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const input = $("#searchInput");
  const button = $("#voiceSearch");
  const status = $("#voiceStatus");
  if (!SpeechRecognition || !window.isSecureContext) {
    // iOS home-screen apps served over plain HTTP cannot request microphone
    // permission for Web Speech. Keep keyboard dictation accessible instead.
    input.focus();
    status.textContent = "Use the microphone on your iPhone keyboard to dictate a search.";
    showToast("Use the microphone on your iPhone keyboard to dictate", 4000);
    return;
  }
  if (voiceRecognition) {
    voiceRecognition.stop();
    return;
  }
  const recognition = new SpeechRecognition();
  voiceRecognition = recognition;
  recognition.lang = navigator.language || "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  button.setAttribute("aria-pressed", "true");
  button.setAttribute("aria-label", "Stop voice search");
  button.classList.add("listening");
  status.textContent = "Listening for your search";
  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript?.trim();
    if (transcript) {
      input.value = transcript;
      status.textContent = "Recognized " + transcript + ". Searching.";
      runSearch();
    }
  };
  recognition.onerror = (event) => {
    status.textContent = "Voice search unavailable: " + (event.error || "unknown error");
    showToast("Voice search unavailable. Use keyboard dictation.", 4000);
  };
  recognition.onend = () => {
    voiceRecognition = null;
    button.classList.remove("listening");
    button.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-label", "Search by voice");
  };
  try {
    recognition.start();
  } catch (error) {
    recognition.onend();
    input.focus();
    showToast("Use keyboard dictation for voice search", 3500);
  }
}

function wire() {
  document.querySelectorAll(".tabbar button[data-view]").forEach((button)=>button.addEventListener("click",()=>setView(button.dataset.view)));
  document.querySelectorAll(".back-now").forEach((b)=>b.addEventListener("click",()=>setView("now")));
  document.querySelectorAll(".provider").forEach((button)=>button.addEventListener("click",()=>{state.provider=button.dataset.provider;document.querySelectorAll(".provider").forEach((n)=>{n.classList.toggle("active",n===button);n.setAttribute("aria-pressed",String(n===button));});}));
  $("#searchButton").addEventListener("click", runSearch);
  $("#voiceSearch").addEventListener("click", startVoiceSearch);
  $("#searchInput").addEventListener("keydown",(event)=>{if(event.key==="Enter")runSearch();});
  $("#openSearch").addEventListener("click",()=>setView("search"));
  $("#destination").addEventListener("click",renderDestinationOverlay);
  $("#more").addEventListener("click",renderBalanceOverlay);
  $("#previous").addEventListener("click",(event)=>transport("previous",event.currentTarget));
  $("#next").addEventListener("click",(event)=>transport("next",event.currentTarget));
  $("#playPause").addEventListener("click",(event)=>transport(selectedQueue()?.state==="playing"?"pause":"play",event.currentTarget));
  $("#shuffle").addEventListener("click",toggleShuffle);
  $("#repeat").addEventListener("click",toggleRepeat);
  $("#mute").addEventListener("click",toggleMute);
  $("#volumeDown").addEventListener("click",()=>nudgeVolume(-1));
  $("#volumeUp").addEventListener("click",()=>nudgeVolume(1));
  $("#volume").addEventListener("input",(event)=>setVolume(event.target.value,false));
  $("#volume").addEventListener("change",(event)=>setVolume(event.target.value,true));
  $("#progress").addEventListener("pointerdown",()=>{state.seeking=true;});
  $("#progress").addEventListener("input",(event)=>{
    state.seeking=true;
    const duration=Number(selectedQueue()?.current_item?.duration||0);
    $("#elapsed").textContent=formatTime(duration*Number(event.target.value)/100);
  });
  $("#progress").addEventListener("change",(event)=>{seekTo(event.target.value);state.seeking=false;});
}

async function boot() {
  wire();
  await refreshState();
  state.pollTimer = window.setInterval(refreshState, 1500);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
}

boot();
