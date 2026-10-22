const APP_VERSION = "0.2.59";
const GROUP_CONFIRM_MS = 30000;
const state = {
  players: [],
  queues: [],
  selectedPlayerId: localStorage.getItem("family-music-player-id") || null,
  view: "now",
  provider: "all",
  searchType: "albums",
  searchProfile: localStorage.getItem("family-music-search-profile") || "all",
  searchRequestId: 0,
  searchResults: null,
  artistFilter: null,
  favoritesProvider: "all",
  favoritesProfile: localStorage.getItem("family-music-favorites-profile") || "all",
  favoritesResults: null,
  favoritesResultsProfile: null,
  favoritesLoadedAt: 0,
  favoritesLoading: false,
  pendingUri: null,
  pollTimer: null,
  pollFailures: 0,
  refreshPromise: null,
  stateAvailable: true,
  reconciliations: new Map(),
  volumeDragging: false,
  lastObservation: null,
  pendingMute: null,
  pendingVolume: null,
  volumeWrites: new Map(),
  artworkFallbackCache: new Map(),
  seeking: false,
  nativeModes: null,
  nativeModesPlayerId: null,
  nativeModesFetchedAt: 0,
  destinationOpen: false,
  balanceOpen: false,
  groupOpen: false,
  pendingGroup: null,
  lastGroupChange: null,
  actionTrace: [],
  transportPending: false,
};

const $ = (selector) => document.querySelector(selector);

async function within(promise, milliseconds) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = window.setTimeout(() => reject(new Error("Response timed out; outcome unknown")), milliseconds);
    })]);
  } finally { window.clearTimeout(timer); }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json", ...(options.headers || {})},
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data?.error) {
    const reference = data.request_id || response.headers?.get("X-Family-Music-Request-ID") || "";
    const error = new Error((data?.error || `HTTP ${response.status}: proxy or non-JSON response`)
      + (reference ? ` (ref ${reference})` : ""));
    error.status = response.status;
    error.requestId = reference;
    error.outcome = data.outcome || "unknown";
    throw error;
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

function maQueueActive(queue = selectedQueue(), player = selectedPlayer()) {
  if (!queue || !["playing", "paused"].includes(queue.state)) return false;
  const queueUri = queue.current_item?.media_item?.uri;
  const physicalUri = player?.current_media?.uri;
  // External Sonos playback can leave a stale MA queue marked active.
  if (queueUri && physicalUri && queueUri !== physicalUri) return false;
  return true;
}

function queueId() {
  return selectedQueue()?.queue_id || selectedPlayer()?.player_id || null;
}

function playerName(player) {
  return player?.name || player?.display_name || player?.player_id || "Speaker";
}

function isPhysicalSonos(player) {
  return Boolean(player?.provider === "sonos" && player?.type !== "group" &&
    String(player?.player_id || "").startsWith("RINCON_"));
}

function sonosGroupContext(player = selectedPlayer()) {
  if (!isPhysicalSonos(player)) return null;
  const leaderId = player.synced_to || player.player_id;
  const leader = state.players.find((item) => item.player_id === leaderId) || player;
  const memberIds = new Set([leaderId]);
  for (const key of ["group_members", "group_childs"]) {
    const values = Array.isArray(leader?.[key]) ? leader[key] : [];
    values.forEach((value) => memberIds.add(value));
  }
  return {leaderId, leader, memberIds};
}

function authoritativeGroupMembership(memberId, player = selectedPlayer()) {
  return Boolean(sonosGroupContext(player)?.memberIds.has(memberId));
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

function artworkUrl(value) {
  if (typeof value !== "string" || !value) return "";
  try {
    const url = new URL(value, window.location.href);
    if (url.origin === window.location.origin) return url.href;
    const host = url.hostname.toLowerCase();
    const isPrivateHost = host === "localhost" || host.endsWith(".local") ||
      /^127\./.test(host) || /^10\./.test(host) ||
      /^192\.168\./.test(host) ||
      /^172\.(1[6-9]|2[0-9]|3[01])\./.test(host);
    if (isPrivateHost && url.port === "1400" && url.pathname === "/getaa") {
      return "/api/sonos-artwork?host=" + encodeURIComponent(url.hostname) +
        "&path=" + encodeURIComponent(url.pathname + url.search);
    }
    if (isPrivateHost && /^\/(imageproxy|api\/imageproxy|api\/image)(\/|\?|$)/.test(url.pathname)) {
      return "/api/artwork?path=" + encodeURIComponent(url.pathname + url.search);
    }
    if (url.protocol === "https:") return url.href;
    return "";
  } catch {
    return "";
  }
}

function artworkFromImage(image) {
  if (typeof image === "string") return artworkUrl(image);
  if (!image || typeof image !== "object") return "";
  // MA returns Image objects with path and proxy_id, not just URL strings.
  // Prefer its authenticated imageproxy endpoint for every provider (including
  // Apple Music and Spotify), so browser/CDN/network differences cannot break art.
  if (typeof image.proxy_id === "string" && /^[a-f0-9]{64}$/i.test(image.proxy_id)) {
    return "/api/artwork?path=" +
      encodeURIComponent("/imageproxy/" + image.proxy_id + "?size=512&fmt=jpg");
  }
  return artworkUrl(image.path);
}

function mediaImage(item) {
  const media = item?.media_item || item || {};
  const images = media.metadata?.images;
  const candidates = [
    item?.image,
    media.image,
    media.image_url,
    ...(Array.isArray(images) ? images : []),
  ];
  for (const candidate of candidates) {
    const url = artworkFromImage(candidate);
    if (url) return url;
  }
  return "";
}

// Sonos-native Apple Music stations can return an inaccessible /getaa URL.
// On image failure, resolve the currently playing track via Music Assistant.
async function recoverSonosArtwork(imageNode, item, originalUrl) {
  if (!originalUrl.startsWith("/api/sonos-artwork?")) return;
  const title = mediaName(item);
  const artist = mediaArtist(item);
  if (!title || !artist || !imageNode.isConnected) return;
  const cacheKey = title.toLowerCase().trim() + "\n" + artist.toLowerCase().trim();
  let lookup = state.artworkFallbackCache.get(cacheKey);
  if (!lookup) {
    lookup = (async () => {
      const query = new URLSearchParams({q: title + " " + artist, provider: "apple"});
      const response = await fetch("/api/search?" + query, {cache: "no-store"});
      if (!response.ok) return "";
      const data = await response.json();
      const matches = Array.isArray(data.tracks) ? data.tracks : [];
      const norm = (value) => String(value || "").toLowerCase().trim();
      const track = matches.find((entry) =>
        norm(entry.name) === norm(title) &&
        (entry.artists || []).some((a) => norm(a.name) === norm(artist))
      );
      return track ? mediaImage(track) : "";
    })().catch(() => "");
    state.artworkFallbackCache.set(cacheKey, lookup);
    if (state.artworkFallbackCache.size > 100) {
      const oldest = state.artworkFallbackCache.keys().next().value;
      state.artworkFallbackCache.delete(oldest);
    }
  }
  const fallback = await lookup;
  if (fallback && fallback !== originalUrl && imageNode.isConnected &&
      imageNode.dataset.artworkSource === originalUrl) {
    imageNode.onerror = null;
    imageNode.src = fallback;
  }
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
  if (view === "favorites") {
    if (state.favoritesResults) showFavorites(false);
    if (!state.favoritesResults || Date.now() - state.favoritesLoadedAt > 30000) {
      void loadFavorites();
    }
  }
  if (view === "recents") loadRecents();
  if (view === "wholehouse") renderWholeHouse();
}

// Read-only household overview: group members never become duplicate sessions.
function wholeHouseSessions() {
  const active = [], idle = [], seen = new Set();
  // Prefer an actively playing Music Assistant group over duplicate physical members.
  const activeGroups = state.players.filter(p => p.type === "group" &&
    p.available !== false && observedPlayback(p) === "playing");
  const groupedIds = new Set(activeGroups.flatMap(p =>
    [...(p.group_members || []), ...(p.group_childs || [])]));
  for (const player of [...activeGroups, ...state.players.filter(p => p.type !== "group")]) {
    if (!isPhysicalSonos(player) && player.type !== "group") continue;
    if (player.available === false) continue;
    if (isPhysicalSonos(player) && groupedIds.has(player.player_id)) continue;
    const leader = playbackPlayer(player);
    if (!leader) continue;
    const id = leader.player_id;
    if (seen.has(id)) continue;
    seen.add(id);
    const members = state.players.filter(p => p.player_id === id ||
      (p.synced_to === id && isPhysicalSonos(p) &&
        [...(leader.group_members || []), ...(leader.group_childs || [])].includes(p.player_id)));
    const label = members.length > 1 ? members.map(playerName).join(" + ") : playerName(leader);
    const playback = observedPlayback(leader);
    const queue = state.queues.find(q => q.queue_id === id || q.queue_id === leader.active_source);
    const queueActive = maQueueActive(queue, leader) && queue?.state === playback;
    const media = queueActive ? (queue?.current_item || leader.current_media) :
      (leader.current_media || queue?.current_item);
    const session = {id, label, playback, media, volume: leader.volume_level};
    (playback === "playing" ? active : idle).push(session);
  }
  return {active, idle};
}

function wholeHouseMarkup(session) {
  const art = mediaImage(session.media);
  return `<button class="whole-house-session" type="button" data-house-player="${escapeHtml(session.id)}">
    <span class="whole-house-art">${art ? `<img src="${escapeHtml(art)}" alt="">` : "♫"}</span>
    <span class="whole-house-copy"><strong>${escapeHtml(session.label)}</strong>
      <span>${escapeHtml(session.playback === "playing" ? mediaName(session.media) : "Not playing")}</span>
      <small>${escapeHtml(session.playback === "playing" ? mediaArtist(session.media) : "Tap to select")}</small></span>
    <span class="whole-house-chevron">›</span></button>`;
}

function renderWholeHouse() {
  const body = $("#wholeHouseBody");
  if (!body || state.view !== "wholehouse") return;
  const {active, idle} = wholeHouseSessions();
  const expanded = body.querySelector("#idleRooms")?.open || false;
  body.innerHTML = `<p class="whole-house-summary">${active.length} active music ${active.length === 1 ? "session" : "sessions"}</p>
    <div class="whole-house-list">${active.length ? active.map(wholeHouseMarkup).join("") :
      '<p class="whole-house-empty">No Sonos rooms are currently playing.</p>'}</div>
    <details id="idleRooms" ${expanded ? "open" : ""}><summary>Other Rooms (${idle.length})</summary>
      <div class="whole-house-list">${idle.map(wholeHouseMarkup).join("")}</div></details>`;
  body.querySelectorAll("[data-house-player]").forEach(button => button.addEventListener("click", () => {
    const id = button.dataset.housePlayer;
    if (!state.players.some(player => player.player_id === id)) return;
    state.selectedPlayerId = id;
    localStorage.setItem("family-music-player-id", id);
    setView("now");
    renderNow();
  }));
}

function renderPlayerSelect() {
  if (!state.players.length) return;
  if (!state.selectedPlayerId) {
    state.selectedPlayerId = state.players[0].player_id;
    localStorage.setItem("family-music-player-id", state.selectedPlayerId);
  }
}

// Playback observations come from MA's player model, never its retained queue.
// A synced physical member follows only a reciprocally observed coordinator.
function playbackPlayer(player = selectedPlayer()) {
  if (!player || player.available === false) return null;
  if (isPhysicalSonos(player) && player.synced_to) {
    const leader = state.players.find(p => p.player_id === player.synced_to);
    const members = [...(leader?.group_members || []), ...(leader?.group_childs || [])];
    return leader?.available !== false && members.includes(player.player_id) ? leader : null;
  }
  return player;
}

function observedPlayback(player = selectedPlayer()) {
  const value = playbackPlayer(player)?.state;
  return state.stateAvailable && ["playing", "paused", "idle", "off"].includes(value) ? value : null;
}

function destinationIsPlaying(player) {
  return observedPlayback(player) === "playing";
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

function currentMediaPosition(item, player) {
  let position = Number(item?.elapsed_time) || 0;
  const updated = Number(item?.elapsed_time_last_updated);
  if (player?.state === "playing" && Number.isFinite(updated) && updated > 0) {
    position += Math.max(0, Date.now() / 1000 - updated);
  }
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
  const queueActive = maQueueActive(queue, player) && queue.state === observedPlayback(player);
  const item = queueActive
    ? (queue?.current_item || player?.current_media || null)
    : (player?.current_media || queue?.current_item || null);
  $("#trackTitle").textContent = mediaName(item);
  $("#trackMeta").textContent = mediaArtist(item) || "—";
  $("#destinationName").textContent = player ? playerName(player) : "Speaker unavailable";

  const artwork = $("#artwork");
  const image = mediaImage(item);
  artwork.innerHTML = image ? `<img src="${escapeHtml(image)}" alt="">` : "<span>♪</span>";
  if (image.startsWith("/api/sonos-artwork?")) {
    const img = artwork.querySelector("img");
    img.dataset.artworkSource = image;
    img.onerror = () => { void recoverSonosArtwork(img, item, image); };
  }

  const playback = observedPlayback(player);
  const playing = playback === "playing";
  $("#playPause").disabled = playback === null;
  $("#playPause").title = playback === null ? "Playback state unavailable" : (playing ? "Pause" : "Play");
  $("#playPause").textContent = playing ? "❚❚" : "▶";

  const pendingMute = state.pendingMute?.playerId === player?.player_id ? state.pendingMute : null;
  const muted = pendingMute ? pendingMute.muted : Boolean(player?.volume_muted);
  $("#mute").textContent = muted ? "🔇" : "🔊";
  $("#mute").setAttribute("aria-pressed", String(muted));

  const volume = effectiveVolume(player);
  if (!state.volumeDragging) $("#volume").value = volume;
  $("#volumeValue").textContent = volume;

  const duration = Number(item?.duration || item?.media_item?.duration || 0);
  const position = Math.min(duration || Infinity, queueActive
    ? currentPosition(queue)
    : currentMediaPosition(item, player));
  if (!state.seeking && document.activeElement !== $("#progress")) $("#progress").value = duration > 0 ? position / duration * 100 : 0;
  $("#elapsed").textContent = formatTime(position);
  $("#remaining").textContent = `-${formatTime(Math.max(0, duration - position))}`;

  const queueControllable = maQueueActive(queue, player);
  const native = !queueControllable && state.nativeModesPlayerId === player?.player_id
    ? state.nativeModes : null;
  const modesAvailable = queueControllable || Boolean(native?.available);
  const previous = $("#previous");
  // The physical Sonos transport is authoritative about station restrictions.
  const physicalCapabilities = state.nativeModesPlayerId === player?.player_id
    ? state.nativeModes : null;
  previous.disabled = physicalCapabilities?.can_previous === false;
  previous.title = previous.disabled
    ? "Previous unavailable for this Sonos source" : "Previous";
  previous.setAttribute("aria-label", previous.title);
  const shuffle = $("#shuffle");
  const repeat = $("#repeat");
  shuffle.disabled = !modesAvailable;
  repeat.disabled = !modesAvailable;
  const shuffleOn = queueControllable ? Boolean(queue.shuffle_enabled) : Boolean(native?.shuffle);
  shuffle.classList.toggle("active", modesAvailable && shuffleOn);
  const repeatMode = queueControllable ? queue.repeat_mode || "off" : native?.repeat || "off";
  repeat.classList.toggle("active", repeatMode !== "off");
  repeat.innerHTML = repeatMode === "one"
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/><text x="12" y="15" font-size="9" text-anchor="middle" fill="currentColor" stroke="none">1</text></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/></svg>';
  shuffle.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m18 14 4 4-4 4M18 2l4 4-4 4M2 18h2c6 0 8-12 14-12h4M2 6h2c2.7 0 4.5 2.4 6 5m4 2c1.5 2.6 3.3 5 6 5h2"/></svg>';
  repeat.setAttribute("aria-label", "Repeat " + (repeatMode === "one" ? "one" : repeatMode === "all" ? "all" : "off"));
  repeat.title = repeat.getAttribute("aria-label");
  shuffle.setAttribute("aria-label", "Shuffle " + (modesAvailable && shuffleOn ? "on" : "off"));
  shuffle.title = shuffle.getAttribute("aria-label");
}

async function refreshState({afterCurrent = false} = {}) {
  if (state.refreshPromise) {
    if (!afterCurrent) return state.refreshPromise;
    await state.refreshPromise;
    // Coalesce callers onto one successor, which starts after command acceptance.
    return refreshState();
  }
  state.refreshPromise = (async () => {
    try {
      const started = Date.now();
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 6000);
      let result;
      try { result = await api("/api/state", {signal: controller.signal, cache: "no-store"}); }
      finally { window.clearTimeout(timeout); }
      if (!Array.isArray(result.players) || !Array.isArray(result.queues)) {
        throw new Error("State observation unavailable");
      }
      state.stateAvailable = true;
      state.stateTiming = {readStartedAt: started,
        sincePreviousReadMs: state.stateTiming ? started - state.stateTiming.readStartedAt : null,
        roundTripMs: Date.now() - started, ...result.timing};
      state.players = Array.isArray(result.players)
        ? result.players.filter((p) => p.available !== false)
        : [];
      state.queues = Array.isArray(result.queues) ? result.queues : [];
      state.pollFailures = 0;
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
        if (Number(actual) === state.pendingVolume.level ||
            Date.now() >= state.pendingVolume.expiresAt) {
          state.pendingVolume = null;
        }
      }
      renderNow();
      renderWholeHouse();
      const player = selectedPlayer();
      const observation = {selected: state.selectedPlayerId, playback: observedPlayback(),
        playerState: player?.state ?? null, queueState: selectedQueue()?.state ?? null,
        volume: player?.type === "group" ? player?.group_volume : player?.volume_level};
      const signature = JSON.stringify(observation);
      if (signature !== state.lastObservation) {
        traceAction("state_observed", {...observation, ...state.stateTiming});
        state.lastObservation = signature;
      }
      updateDestinationActivity();
      if (state.lastGroupChange?.outcome === "unknown") {
        reconcileGroupChange(state.lastGroupChange);
      }
      updateGroupOverlay();
      void refreshNativeModes();
      return true;
    } catch (error) {
      state.stateAvailable = false;
      renderNow();
      state.pollFailures = Math.min(state.pollFailures + 1, 4);
      showToast(error.message, 3000);
      return false;
    } finally {
      state.refreshPromise = null;
    }
  })();
  return state.refreshPromise;
}

// Only observations confirm a command. Up to seven reads in a six-second window
// (plus bounded in-flight reads), shared with polling. Never retry commands.
async function reconcileState(key, playerId, matches, requestId = null) {
  const token = {};
  state.reconciliations.set(key, token);
  const deadline = Date.now() + 6000;
  try {
    for (let attempt = 0; attempt < 7; attempt++) {
      if (state.reconciliations.get(key) !== token || state.selectedPlayerId !== playerId) return;
      const fresh = await refreshState({afterCurrent: attempt === 0});
      if (state.reconciliations.get(key) !== token || state.selectedPlayerId !== playerId) return;
      if (fresh && matches()) {
        traceAction("command_observed", {key, requestId, selected: playerId});
        return;
      }
      if (!fresh || Date.now() >= deadline || attempt === 6) break;
      await new Promise(resolve => window.setTimeout(resolve, 1000));
    }
    traceAction("confirmation_unavailable", {key, requestId, selected: playerId});
    showToast("Command accepted; state not yet confirmed", 3000);
  } finally {
    if (state.reconciliations.get(key) === token) state.reconciliations.delete(key);
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
  if (item?.saved_library) return "Saved playlist · " + (providerOf(item) === "spotify" ? "Spotify" : "Apple Music");
  return artists || item?.album?.name || item?.media_type || "";
}

function itemMarkup(item, row = false) {
  const uri = escapeHtml(item?.uri || "");
  const name = escapeHtml(item?.name || "Music");
  const subtitle = escapeHtml(itemSubtitle(item));
  const imageUrl = mediaImage(item);
  const image = imageUrl
    ? `<img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" decoding="async">`
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
  // Artwork proxy requests can be temporarily rejected during a large grid load.
  // Retry failed images with bounded backoff instead of leaving blank tiles.
  container.querySelectorAll(".playable img").forEach((img) => {
    let attempts = 0;
    const original = img.getAttribute("src");
    if (!original?.startsWith("/api/artwork?")) return;
    img.addEventListener("error", () => {
      if (++attempts > 3 || !img.isConnected) return;
      setTimeout(() => {
        if (!img.isConnected) return;
        const separator = original.includes("?") ? "&" : "?";
        img.src = original + separator + "retry=" + attempts;
      }, 750 * attempts * attempts);
    });
  });
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
    ["Sonos Favorites", data.sonos_favorites],
  ].filter(([, items]) => Array.isArray(items) && items.length);
  if (!sections.length) {
    target.innerHTML = '<div class="empty">Nothing here yet.</div>';
    return;
  }
  target.innerHTML = sections
    .map(([title, items]) => `<section class="section"><h3>${title}</h3><div class="grid">${items.map((item) => itemMarkup(item)).join("")}</div></section>`)
    .join("");
  target.querySelectorAll(".grid img").forEach((img, index) => {
    if (index < 4) {
      img.loading = "eager";
      img.fetchPriority = "high";
    }
  });
  wirePlayable(target, sections.map(([, items]) => items));
}

function providerOf(item) {
  if (item?.favorite_source) return item.favorite_source;
  if (item?.sonos_favorite || String(item?.uri || "").startsWith("sonos-favorite://")) return "sonos";
  const providers = item?.providers || [];
  if (providers.includes("apple_music")) return "apple";
  if (providers.includes("spotify")) return "spotify";
  const uri = String(item?.uri || "");
  if (uri.startsWith("apple_music://")) return "apple";
  if (uri.startsWith("spotify://")) return "spotify";
  return "library";
}

// Provider-instance mapping is a playback/library route, not proof of who favorited
// a shared item. Preserve items mapped to both profiles in both views.
const SPOTIFY_PROFILES = Object.freeze({
  david: "spotify--Q5Eakxzw",
  kaelah: "spotify--ooVGVwHn",
});

function filterFavoriteProfile(data, profile, provider = "all") {
  if (profile === "all") return data;
  const instance = SPOTIFY_PROFILES[profile];
  if (!instance) return data;
  // Apple Music has one configured account (David); native Sonos favorites
  // are shared household favorites. Both belong in David's combined view.
  // Music Assistant's cross-service provider mappings must never attribute
  // Apple Music items to Kaelah's Spotify account.
  const davidLibraryPlaylistIds = new Set(["19", "21", "22", "23", "25", "27"]);
  return Object.fromEntries(Object.entries(data || {}).map(([kind, items]) => [kind,
    (items || []).filter((item) => {
      const source = providerOf(item);
      if (source === "apple" || source === "sonos") return profile === "david";
      if (source === "spotify" || source === "library") {
        if (kind === "playlists") {
          const id = String(item?.uri || "").match(/^library:\/\/playlist\/(\d+)$/)?.[1];
          if (id && davidLibraryPlaylistIds.has(id)) return profile === "david";
          if (profile === "david") return false;
        }
        return Array.isArray(item?.provider_instances) && item.provider_instances.includes(instance);
      }
      return false;
    }),
  ]));
}

function filterFavoriteProvider(data, provider) {
  if (provider === "all") return data;
  const filtered = {};
  for (const [kind, items] of Object.entries(data || {})) {
    filtered[kind] = (items || []).filter((item) => providerOf(item) === provider);
  }
  return filtered;
}

function showFavorites(force = true) {
  const body = $("#favoritesBody");
  if (!state.favoritesResults) return;
  // Keep existing image nodes intact when merely returning to this tab.
  if (!force && body.querySelector(".media-item, .recent-item")) return;
  if (state.favoritesResultsProfile !== state.favoritesProfile) return;
  renderSections(body, filterFavoriteProvider(state.favoritesResults, state.favoritesProvider));
}

async function loadFavorites() {
  if (state.favoritesLoading) return;
  state.favoritesLoading = true;
  const requestedProfile = state.favoritesProfile;
  const body = $("#favoritesBody");
  if (!state.favoritesResults) {
    body.innerHTML = '<div class="empty">Loading favorites…</div>';
  }
  try {
    const data = await api(`/api/favorites?profile=${encodeURIComponent(requestedProfile)}`);
    if (requestedProfile !== state.favoritesProfile) return;
    state.favoritesResults = data;
    state.favoritesResultsProfile = requestedProfile;
    state.favoritesLoadedAt = Date.now();
    showFavorites();
  } catch (error) {
    if (requestedProfile === state.favoritesProfile) {
      body.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
  } finally {
    state.favoritesLoading = false;
    if (requestedProfile !== state.favoritesProfile) loadFavorites();
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

const SEARCH_TYPES = {
  artists: "artists", albums: "albums", songs: "tracks",
  playlists: "playlists", radio: "radio",
};

function showSearchResults() {
  const body = $("#searchBody");
  const key = SEARCH_TYPES[state.searchType] || "albums";
  const results = state.searchResults?.[key] || [];
  const items = state.searchType === "albums" && state.artistFilter
    ? results.filter((item) => (item.artists || []).some((artist) =>
        String(artist.name || "").toLocaleLowerCase() === state.artistFilter.toLocaleLowerCase()))
    : results;
  const title = {artists: "Artists", albums: "Albums", songs: "Songs",
    playlists: "Playlists", radio: "Stations"}[state.searchType] || "Albums";
  if (!items.length) {
    body.innerHTML = '<div class="empty">No ' + title.toLowerCase() + ' found. Try another search or provider.</div>';
    return;
  }
  body.innerHTML = '<section class="section"><h3>' + title + ' (' + items.length +
    ')</h3><div class="grid">' + items.map((item) => itemMarkup(item)).join("") +
    '</div></section>';
  if (state.searchType === "artists") {
    body.querySelectorAll(".playable").forEach((button) => {
      button.addEventListener("click", () => {
        const artist = findItem(button.dataset.uri, [items]);
        if (!artist) return;
        $("#searchInput").value = artist.name;
        state.artistFilter = artist.name;
        state.searchType = "albums";
        syncSearchType();
        runSearch();
      });
    });
  } else {
    wirePlayable(body, [items]);
  }
}

function syncSearchType() {
  document.querySelectorAll(".search-type").forEach((button) => {
    const active = button.dataset.type === state.searchType;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

async function runSearch() {
  const query = $("#searchInput").value.trim();
  if (!query) return;
  if (state.artistFilter && query.toLocaleLowerCase() !== state.artistFilter.toLocaleLowerCase()) state.artistFilter = null;
  const body = $("#searchBody");
  const requestId = ++state.searchRequestId;
  body.innerHTML = '<div class="empty">Searching…</div>';
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(query)}&provider=${encodeURIComponent(state.provider)}&profile=${encodeURIComponent(state.searchProfile)}`);
    if (requestId !== state.searchRequestId) return;
    state.searchResults = data;
    showSearchResults();
  } catch (error) {
    if (requestId === state.searchRequestId) body.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

function traceAction(phase, detail = {}) {
  const entry = {time: new Date().toISOString(), version: APP_VERSION, phase, ...detail};
  state.actionTrace = [...state.actionTrace, entry].slice(-30);
  console.info("[Family Music standalone]", entry);
  try { sessionStorage.setItem("family-music-action-trace", JSON.stringify(state.actionTrace)); }
  catch (_) { console.warn("Family Music diagnostic storage unavailable; console trace retained"); }
  const panel = $("#actionDiagnostics");
  if (panel) panel.textContent = JSON.stringify(state.actionTrace, null, 2);
}

async function transport(action, button) {
  const qid = queueId();
  if (!qid || state.transportPending || !state.stateAvailable) return;
  const playerId = state.selectedPlayerId;
  const requestId = crypto.randomUUID();
  state.transportPending = true;
  button.setAttribute("aria-busy", "true");
  traceAction("transport_start", {requestId, selected: state.selectedPlayerId, queue: qid, action});
  try {
    await within(api("/api/transport", {
      method: "POST",
      body: JSON.stringify({queue_id: qid, action, request_id: requestId}),
    }), 15000);
    traceAction("transport_accepted", {requestId});
    button.classList.add("accepted");
    window.setTimeout(() => button.classList.remove("accepted"), 450);
    if (action === "play" || action === "pause") {
      await reconcileState("transport", playerId,
        () => action === "play" ? observedPlayback() === "playing" : observedPlayback() === "paused", requestId);
    } else {
      await refreshState({afterCurrent: true});
    }
  } catch (error) {
    traceAction("transport_error", {requestId, status: error.status || "network",
      originReference: error.requestId || "none"});
    showToast(`${error.message} (action ${requestId})`, 6000);
  } finally {
    state.transportPending = false;
    button.removeAttribute("aria-busy");
  }
}

function queueVolumeWrite(playerId, level, flush = false) {
  state.reconciliations.delete("volume");
  const bounded = Math.max(0, Math.min(100, Math.round(Number(level))));
  let writer = state.volumeWrites.get(playerId);
  if (!writer) {
    writer = {timer: null, inFlight: false, pendingLevel: null};
    state.volumeWrites.set(playerId, writer);
  }
  writer.pendingLevel = bounded;
  const drain = async () => {
    if (writer.inFlight || writer.pendingLevel === null) return;
    if (writer.timer) {
      clearTimeout(writer.timer);
      writer.timer = null;
    }
    const nextLevel = writer.pendingLevel;
    writer.pendingLevel = null;
    writer.inFlight = true;
    let accepted = false;
    try {
      await api("/api/volume", {
        method: "POST",
        body: JSON.stringify({player_id: playerId, level: nextLevel}),
      });
      accepted = true;
    } catch (error) {
      if (state.pendingVolume?.playerId === playerId) state.pendingVolume = null;
      showToast(error.message, 3000);
    } finally {
      writer.inFlight = false;
      if (writer.pendingLevel !== null) {
        writer.timer = window.setTimeout(drain, 0);
      } else {
        if (state.pendingVolume?.playerId === playerId) state.pendingVolume = null;
        renderNow();
        if (accepted) void reconcileState("volume", playerId, () => {
          const p = state.players.find(item => item.player_id === playerId);
          const actual = p?.type === "group" ? p?.group_volume : p?.volume_level;
          return p?.available !== false && actual != null && Number(actual) === nextLevel;
        });
      }
    }
  };
  if (flush) {
    if (writer.timer) clearTimeout(writer.timer);
    writer.timer = null;
    void drain();
  } else if (!writer.timer) {
    writer.timer = window.setTimeout(drain, 45);
  }
}

function setVolume(level, flush = false) {
  const player = selectedPlayer();
  if (!player) return;
  const bounded = Math.max(0, Math.min(100, Math.round(Number(level))));
  state.pendingVolume = {
    playerId: player.player_id,
    level: bounded,
    expiresAt: Date.now() + 5000,
  };
  $("#volume").value = bounded;
  $("#volumeValue").textContent = bounded;
  queueVolumeWrite(player.player_id, bounded, flush);
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
  const queue = selectedQueue();
  const maActive = maQueueActive(queue);
  const endpoint = maActive ? "/api/queue-control" : "/api/native-mode-control";
  try {
    await api(endpoint, {method:"POST", body:JSON.stringify({queue_id:qid, action, value})});
    state.nativeModesFetchedAt = 0;
    await refreshState();
    await refreshNativeModes();
  } catch (error) { showToast(error.message, 3000); }
}

function toggleShuffle() {
  const queue = selectedQueue();
  const maActive = queue && ["playing", "paused"].includes(queue.state);
  const current = maActive ? Boolean(queue.shuffle_enabled) : Boolean(state.nativeModes?.shuffle);
  queueControl("shuffle", !current);
}

function toggleRepeat() {
  const queue = selectedQueue();
  const maActive = queue && ["playing", "paused"].includes(queue.state);
  const current = maActive ? queue.repeat_mode || "off" : state.nativeModes?.repeat || "off";
  const next = current === "off" ? "one" : current === "one" ? "all" : "off";
  queueControl("repeat", next);
}

async function refreshNativeModes() {
  const player = selectedPlayer();
  if (!player) return;
  if (Date.now() - state.nativeModesFetchedAt < 12000
      && state.nativeModesPlayerId === player.player_id) return;
  state.nativeModesFetchedAt = Date.now();
  state.nativeModesPlayerId = player.player_id;
  if (!player.player_id.startsWith("RINCON_")) {
    state.nativeModes = null;
    return;
  }
  try {
    const modes = await api("/api/native-modes?queue_id=" + encodeURIComponent(player.player_id));
    if (state.nativeModesPlayerId === player.player_id) state.nativeModes = modes;
  } catch (_) {
    state.nativeModes = null;
  }
  renderNow();
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
  state.groupOpen = false;
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

function groupButtonState(memberId) {
  const context = sonosGroupContext();
  const pending = state.pendingGroup?.memberId === memberId ? state.pendingGroup : null;
  const member = context?.memberIds.has(memberId) || false;
  if (pending && Date.now() < pending.expiresAt) {
    return {
      checked: pending.action === "join",
      pending: true,
      label: pending.action === "join" ? "Joining…" : "Leaving…",
    };
  }
  return {checked: member, pending: false, label: member ? "Joined" : "Add"};
}

function updateGroupOverlay() {
  if (!state.groupOpen) return;
  const context = sonosGroupContext();
  if (!context) {
    closeOverlay();
    return;
  }
  document.querySelectorAll(".group-speaker-option[data-member]").forEach((button) => {
    const memberId = button.dataset.member;
    const current = groupButtonState(memberId);
    button.classList.toggle("joined", current.checked);
    button.classList.toggle("pending", current.pending);
    button.setAttribute("aria-pressed", String(current.checked));
    button.setAttribute("aria-busy", String(current.pending));
    const mark = button.querySelector(".group-speaker-mark");
    const status = button.querySelector(".group-speaker-status");
    if (mark) mark.textContent = current.pending ? "◌" : current.checked ? "✓" : "○";
    if (status) status.textContent = current.label;
  });
}

function groupBalanceMarkup(memberIds) {
  const members = Array.from(memberIds)
    .map((id) => state.players.find((player) => player.player_id === id))
    .filter(Boolean);
  if (members.length < 2) return "";
  const avg = Math.round(
    members.reduce((sum, player) => sum + effectiveVolume(player), 0) / members.length
  );
  return `
    <div class="group-balance-section">
      <h4>Speaker Balance</h4>
      <div class="balance-help">Shape where the sound comes from without switching speakers.</div>
      ${members.map((player) => {
        const trim = Math.max(
          -12,
          Math.min(12, Math.round((effectiveVolume(player) - avg) / 2))
        );
        return `<div class="balance-row">
          <div class="balance-name">${escapeHtml(playerName(player))}</div>
          <input class="member-trim" data-member="${escapeHtml(player.player_id)}"
            data-base="${avg}" type="range" min="-12" max="12" step="1" value="${trim}"
            aria-label="${escapeHtml(playerName(player))} balance">
        </div>`;
      }).join("")}
      <div class="balance-scale"><span>Less</span><span>Neutral</span><span>More</span></div>
      <button class="reset-balance" data-reset-balance>Reset Balance</button>
    </div>
  `;
}

function wireMemberTrimControls() {
  document.querySelectorAll(".member-trim").forEach((slider) => {
    const apply = () => {
      const target = Math.max(
        0,
        Math.min(100, Number(slider.dataset.base) + Number(slider.value) * 2)
      );
      const player = state.players.find(
        (item) => item.player_id === slider.dataset.member
      );
      if (!player) return;
      state.pendingVolume = {
        playerId: player.player_id,
        level: target,
        expiresAt: Date.now() + 5000,
      };
      queueVolumeWrite(player.player_id, target, false);
    };
    slider.addEventListener("input", () => apply(false));
    slider.addEventListener("change", () => {
      const target = Math.max(
        0,
        Math.min(100, Number(slider.dataset.base) + Number(slider.value) * 2)
      );
      queueVolumeWrite(slider.dataset.member, target, true);
    });
  });
  document.querySelectorAll("[data-reset-balance]").forEach((button) => {
    button.addEventListener("click", () => {
      button.closest(".group-balance-section, .popover")
        ?.querySelectorAll(".member-trim")
        .forEach((slider) => {
          slider.value = 0;
          slider.dispatchEvent(new Event("change"));
        });
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function observeGroupChange(operation) {
  const anchor = state.players.find((p) => p.player_id === operation.anchorId);
  const member = state.players.find((p) => p.player_id === operation.memberId);
  const context = sonosGroupContext(anchor);
  if (!anchor || !member || !context) return null;
  const leader = state.players.find((p) => p.player_id === context.leaderId);
  if (!leader || leader.available === false) return null;
  const listed = context.memberIds.has(operation.memberId);
  // Require reciprocal topology, not just an old coordinator's member list.
  const memberLeader = member.synced_to || member.player_id;
  if (operation.action === "join") {
    return listed && memberLeader === context.leaderId ? context : null;
  }
  return !listed && memberLeader !== context.leaderId ? context : null;
}

function reconcileGroupChange(operation) {
  const context = observeGroupChange(operation);
  if (!context) return false;
  operation.outcome = "confirmed";
  if (operation.followSuccessor && state.selectedPlayerId === operation.playerId) {
    state.selectedPlayerId = context.leaderId;
    localStorage.setItem("family-music-player-id", state.selectedPlayerId);
  }
  traceAction("group_confirmed", {requestId: operation.requestId, leader: context.leaderId});
  return true;
}

async function waitForGroupMembership(memberId, action, operation = state.pendingGroup) {
  if (!operation) return false;
  while (Date.now() < operation.expiresAt) {
    let fresh = false;
    try { fresh = await within(refreshState(), Math.max(1, operation.expiresAt - Date.now())); }
    catch (_) { break; }
    if (fresh && reconcileGroupChange(operation)) {
      state.pendingGroup = null;
      if (state.groupOpen) renderGroupOverlay();
      return true;
    }
    await sleep(1500);
  }
  return false;
}

async function toggleGroupMember(memberId) {
  const player = selectedPlayer();
  const context = sonosGroupContext(player);
  if (!player || !context || state.pendingGroup) return;
  const action = context.memberIds.has(memberId) ? "leave" : "join";
  if (action === "leave" && context.memberIds.size <= 1) return;
  const survivors = [...context.memberIds].filter((id) => id !== memberId).sort();
  const operation = {memberId, action, playerId: player.player_id,
    anchorId: action === "leave" && memberId === context.leaderId ? survivors[0] : player.player_id,
    followSuccessor: action === "leave" && memberId === context.leaderId,
    requestId: crypto.randomUUID(), outcome: "sending", expiresAt: Infinity};
  state.pendingGroup = operation;
  state.lastGroupChange = operation;
  updateGroupOverlay();
  traceAction("group_start", {requestId: operation.requestId, selected: player.player_id, memberId, action});
  try {
    const result = await within(api("/api/group-member", {
      method: "POST",
      body: JSON.stringify({player_id: player.player_id, member_id: memberId, action,
        request_id: operation.requestId}),
    }), 30000);
    operation.outcome = "accepted";
    // Start the observation budget after command acceptance, not before it.
    operation.expiresAt = Date.now() + Math.min(result.confirmation_timeout_ms || GROUP_CONFIRM_MS, GROUP_CONFIRM_MS);
    traceAction("group_accepted", {requestId: operation.requestId});
  } catch (error) {
    if (error.outcome === "rejected") {
      operation.outcome = "rejected";
      state.pendingGroup = null;
      showToast(error.message, 5000);
      traceAction("group_rejected", {requestId: operation.requestId});
      updateGroupOverlay();
      return;
    }
    // A lost response cannot tell us whether the command executed. Observe;
    // never issue a second join/leave automatically.
    operation.outcome = "unknown";
    operation.expiresAt = Date.now() + GROUP_CONFIRM_MS;
    traceAction("group_response_unknown", {requestId: operation.requestId});
  }
  const confirmed = await waitForGroupMembership(memberId, action, operation);
  state.pendingGroup = null;
  if (!confirmed) {
    operation.outcome = "unknown";
    showToast("Group command outcome unknown; refresh topology before retrying", 6000);
    traceAction("group_confirmation_unavailable", {requestId: operation.requestId});
  }
  updateGroupOverlay();
}

function renderGroupOverlay() {
  const player = selectedPlayer();
  const context = sonosGroupContext(player);
  if (!context) {
    renderBalanceOverlay();
    return;
  }
  state.groupOpen = true; state.destinationOpen = false; state.balanceOpen = false;
  const speakers = state.players.filter((item) => isPhysicalSonos(item));
  const rows = speakers.map((member) => {
    const current = groupButtonState(member.player_id);
    const coordinator = member.player_id === context.leaderId;
    const onlyMember = coordinator && context.memberIds.size <= 1;
    const detail = coordinator
      ? (onlyMember ? "Current playback" : "Current playback · tap to remove")
      : "Tap to " + (current.checked ? "remove" : "add");
    return `<button class="group-speaker-option ${current.checked?"joined":""}" data-member="${escapeHtml(member.player_id)}" aria-pressed="${current.checked}" ${onlyMember?"disabled":""}>
      <span class="group-speaker-mark">${current.checked?"✓":"○"}</span>
      <span class="group-speaker-copy"><strong>${escapeHtml(playerName(member))}</strong><small>${detail}</small></span>
      <span class="group-speaker-status">${coordinator?"Playing":current.label}</span>
    </button>`;
  }).join("");
  const balance = groupBalanceMarkup(context.memberIds);
  $("#overlay").classList.add("open");
  $(".tabbar")?.classList.add("overlay-hidden");
  $("#overlay").innerHTML = `<button class="scrim" aria-label="Close"></button><div class="popover"><h3>Group Speakers</h3><div class="group-help">Add or remove speakers from this synchronized Sonos session.</div><div class="group-speaker-list">${rows}</div>${balance}</div>`;
  $(".scrim").addEventListener("click", closeOverlay);
  document.querySelectorAll(".group-speaker-option[data-member]").forEach((button) => {
    if (!button.disabled) button.addEventListener("click", () => toggleGroupMember(button.dataset.member));
  });
  wireMemberTrimControls();
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
      '<div class="balance-scale"><span>Less</span><span>Neutral</span><span>More</span></div><button class="reset-balance" data-reset-balance>Reset Balance</button>';
  }
  $("#overlay").classList.add("open");
  $(".tabbar")?.classList.add("overlay-hidden");
  $("#overlay").innerHTML = `<button class="scrim" aria-label="Close"></button><div class="popover"><h3>Speaker Balance</h3>${body}</div>`;
  $(".scrim").addEventListener("click", closeOverlay);
  wireMemberTrimControls();
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
  document.querySelectorAll(".search-profile").forEach((button)=>{
    const active = button.dataset.searchProfile === state.searchProfile;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
    button.addEventListener("click",()=>{
      state.searchProfile = button.dataset.searchProfile;
      localStorage.setItem("family-music-search-profile", state.searchProfile);
      document.querySelectorAll(".search-profile").forEach((node)=>{
        node.classList.toggle("active", node === button);
        node.setAttribute("aria-pressed", String(node === button));
      });
      state.searchResults = null;
      if ($("#searchInput").value.trim()) runSearch();
    });
  });
  document.querySelectorAll(".provider").forEach((button)=>button.addEventListener("click",()=>{state.provider=button.dataset.provider;document.querySelectorAll(".provider").forEach((n)=>{n.classList.toggle("active",n===button);n.setAttribute("aria-pressed",String(n===button));});if(state.searchResults)runSearch();}));
  document.querySelectorAll(".search-type").forEach((button)=>button.addEventListener("click",()=>{state.searchType=button.dataset.type;if(state.searchType!=="albums")state.artistFilter=null;syncSearchType();if(state.searchResults)showSearchResults();}));
  document.querySelectorAll(".favorite-profile").forEach((button)=>{
    const active = button.dataset.favoriteProfile === state.favoritesProfile;
    button.classList.toggle("active",active);
    button.setAttribute("aria-pressed",String(active));
    button.addEventListener("click",()=>{
      state.favoritesProfile = button.dataset.favoriteProfile;
      localStorage.setItem("family-music-favorites-profile",state.favoritesProfile);
      document.querySelectorAll(".favorite-profile").forEach((n)=>{
        const selected = n === button;
        n.classList.toggle("active",selected);
        n.setAttribute("aria-pressed",String(selected));
      });
      state.favoritesResults = null;
      state.favoritesResultsProfile = null;
      $("#favoritesBody").innerHTML = '<div class="empty">Loading favorites…</div>';
      loadFavorites();
    });
  });
  document.querySelectorAll(".favorite-provider").forEach((button)=>button.addEventListener("click",()=>{state.favoritesProvider=button.dataset.favoriteProvider;document.querySelectorAll(".favorite-provider").forEach((n)=>{n.classList.toggle("active",n===button);n.setAttribute("aria-pressed",String(n===button));});showFavorites();}));
  $("#searchButton").addEventListener("click", runSearch);
  $("#voiceSearch").addEventListener("click", startVoiceSearch);
  $("#searchInput").addEventListener("keydown",(event)=>{if(event.key==="Enter")runSearch();});
  $("#openSearch").addEventListener("click",()=>setView("search"));
  $("#destination").addEventListener("click",renderDestinationOverlay);
  $("#more").addEventListener("click",()=>isPhysicalSonos(selectedPlayer())?renderGroupOverlay():renderBalanceOverlay);
  $("#previous").addEventListener("click",(event)=>transport("previous",event.currentTarget));
  $("#next").addEventListener("click",(event)=>transport("next",event.currentTarget));
  $("#playPause").addEventListener("click",(event)=>transport(observedPlayback()==="playing"?"pause":"play",event.currentTarget));
  $("#shuffle").addEventListener("click",toggleShuffle);
  $("#repeat").addEventListener("click",toggleRepeat);
  $("#mute").addEventListener("click",toggleMute);
  $("#volumeDown").addEventListener("click",()=>nudgeVolume(-1));
  $("#volumeUp").addEventListener("click",()=>nudgeVolume(1));
  $("#volume").addEventListener("pointerdown",()=>{state.volumeDragging=true;});
  for (const event of ["pointerup", "pointercancel", "blur"]) {
    // Do not rewrite the range value on pointerup: change still needs its final
    // input value. The next observation will render it, even if focus remains.
    $("#volume").addEventListener(event,()=>{state.volumeDragging=false;});
  }
  $("#volume").addEventListener("input",(event)=>setVolume(event.target.value,false));
  $("#volume").addEventListener("change",(event)=>{state.volumeDragging=false;setVolume(event.target.value,true);});
  $("#progress").addEventListener("pointerdown",()=>{state.seeking=true;});
  $("#progress").addEventListener("input",(event)=>{
    state.seeking=true;
    const duration=Number(selectedQueue()?.current_item?.duration||0);
    $("#elapsed").textContent=formatTime(duration*Number(event.target.value)/100);
  });
  $("#progress").addEventListener("change",(event)=>{seekTo(event.target.value);state.seeking=false;});
}

function schedulePoll(delay = 1500) {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  state.pollTimer = window.setTimeout(async () => {
    const ok = await refreshState();
    const nextDelay = ok
      ? 1500
      : Math.min(12000, 1500 * (2 ** state.pollFailures));
    schedulePoll(nextDelay);
  }, delay);
}

async function boot() {
  wire();
  document.documentElement.dataset.familyMusicVersion = APP_VERSION;
  const info = $("#buildInfo");
  if (info) info.textContent = `Standalone frontend ${APP_VERSION}; checking backend…`;
  try {
    const backend = await api("/api/info");
    if (backend.frontend !== "standalone" || typeof backend.version !== "string") {
      throw new Error("Backend version probe returned an unexpected response");
    }
    if (info) info.textContent = `Standalone frontend ${APP_VERSION}; backend ${backend.version}`;
    traceAction("client_loaded", {backendVersion: backend.version});
  } catch (_) {
    if (info) info.textContent = `Standalone frontend ${APP_VERSION}; backend version unavailable`;
    console.warn("Family Music backend version probe failed");
  }
  await refreshState();
  schedulePoll(1500);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js");
}

boot();
