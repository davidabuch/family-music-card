const state = {
  players: [],
  queues: [],
  selectedPlayerId: localStorage.getItem("family-music-player-id") || null,
  view: "now",
  provider: "all",
  pendingUri: null,
  pollTimer: null,
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
  const select = $("#playerSelect");
  if (!state.players.length) {
    select.innerHTML = '<option>No speakers</option>';
    return;
  }
  if (!state.selectedPlayerId || !state.players.some((p) => p.player_id === state.selectedPlayerId)) {
    state.selectedPlayerId = state.players[0].player_id;
    localStorage.setItem("family-music-player-id", state.selectedPlayerId);
  }
  select.innerHTML = state.players
    .map((player) => `<option value="${escapeHtml(player.player_id)}">${escapeHtml(playerName(player))}</option>`)
    .join("");
  select.value = state.selectedPlayerId;
}

function renderNow() {
  const player = selectedPlayer();
  const queue = selectedQueue();
  const item = queue?.current_item || player?.current_media || null;
  $("#trackTitle").textContent = mediaName(item);
  $("#trackMeta").textContent = mediaArtist(item) || playerName(player);

  const artwork = $("#artwork");
  const image = mediaImage(item);
  artwork.innerHTML = image ? `<img src="${escapeHtml(image)}" alt="">` : "<span>♪</span>";

  const playing =
    queue?.state === "playing" ||
    player?.playback_state === "playing" ||
    player?.state === "playing";
  $("#playPause").textContent = playing ? "❚❚" : "▶";

  const volume = Number(player?.volume_level);
  if (Number.isFinite(volume)) {
    const bounded = Math.max(0, Math.min(100, Math.round(volume)));
    if (document.activeElement !== $("#volume")) $("#volume").value = bounded;
    $("#volumeValue").textContent = bounded;
  }
}

async function refreshState() {
  try {
    const result = await api("/api/state");
    state.players = Array.isArray(result.players) ? result.players.filter((p) => p.available !== false) : [];
    state.queues = Array.isArray(result.queues) ? result.queues : [];
    renderPlayerSelect();
    renderNow();
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
  $("#volume").value = bounded;
  $("#volumeValue").textContent = bounded;
  const send = async () => {
    volumeTimer = null;
    try {
      await api("/api/volume", {
        method: "POST",
        body: JSON.stringify({player_id: player.player_id, level: bounded}),
      });
    } catch (error) {
      showToast(error.message, 3000);
    }
  };
  if (flush) {
    clearTimeout(volumeTimer);
    send();
  } else {
    clearTimeout(volumeTimer);
    volumeTimer = window.setTimeout(send, 45);
  }
}

function wire() {
  $("#playerSelect").addEventListener("change", (event) => {
    state.selectedPlayerId = event.target.value;
    localStorage.setItem("family-music-player-id", state.selectedPlayerId);
    renderNow();
    if (state.view === "recents") loadRecents();
  });
  document.querySelectorAll(".tabbar button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.view));
  });
  document.querySelectorAll(".provider").forEach((button) => {
    button.addEventListener("click", () => {
      state.provider = button.dataset.provider;
      document.querySelectorAll(".provider").forEach((node) => node.classList.toggle("active", node === button));
    });
  });
  $("#searchButton").addEventListener("click", runSearch);
  $("#searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runSearch();
  });
  $("#previous").addEventListener("click", (event) => transport("previous", event.currentTarget));
  $("#next").addEventListener("click", (event) => transport("next", event.currentTarget));
  $("#playPause").addEventListener("click", (event) => {
    const queue = selectedQueue();
    const action = queue?.state === "playing" ? "pause" : "play";
    transport(action, event.currentTarget);
  });
  $("#volume").addEventListener("input", (event) => setVolume(event.target.value, false));
  $("#volume").addEventListener("change", (event) => setVolume(event.target.value, true));
}

async function boot() {
  wire();
  await refreshState();
  state.pollTimer = window.setInterval(refreshState, 1500);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
}

boot();
