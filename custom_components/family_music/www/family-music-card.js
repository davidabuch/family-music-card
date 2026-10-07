const CARD_VERSION = "0.3.11";

class FamilyMusicCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = null;
    this._selectedPlayer = null;
    this._provider = "apple";
    this._view = "now";
    this._query = "";
    this._searchResults = null;
    this._selectedArtist = null;
    this._selectedAlbum = null;
    this._albumTracks = [];
    this._recents = [];
    this._favorites = null;
    this._artistReturnView = "search";
    this._albumReturnView = "search";
    this._busy = false;
    this._refreshTimer = null;
    this._refreshTick = 0;
    this._refreshInFlight = false;
    this._seeking = false;
    this._groupMembers = [];
    this._groupMembersFor = null;
    this._groupMembersLoading = false;
    this._membersExpanded = false;
    this._mixBaselinePercent = null;
    this._memberTrimValues = new Map();
    this._optimisticPlayback = null;
    this._optimisticPlaybackUntil = 0;
    this._optimisticVolumes = new Map();
    this._volumeWrites = new Map();
    this._destinationOpen = false;
    this._moreOpen = false;
    this._speechRecognition = null;
    this._dictating = false;
    this._optimisticShuffle = null;
    this._optimisticShuffleUntil = 0;
    this._optimisticRepeat = null;
    this._optimisticRepeatUntil = 0;
    this._optimisticMute = null;
    this._optimisticMuteUntil = 0;
    this._nativePlayerPeers = {};
    this._nativePeerLoadInFlight = false;
  }

  setConfig(config) {
    if (!config?.config_entry_id) {
      throw new Error("config_entry_id is required");
    }
    if (!Array.isArray(config.players) || config.players.length === 0) {
      throw new Error("players is required");
    }
    this._config = config;
    const saved = localStorage.getItem("family-music-card-player");
    this._selectedPlayer = saved && config.players.includes(saved)
      ? saved
      : config.players[0];
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (!this._selectedPlayer || !hass.states[this._selectedPlayer]) {
      this._selectedPlayer =
        this._config.players.find((entityId) => hass.states[entityId]) ||
        this._config.players[0];
    }
    this._syncPlayerSelector();
    this._loadNativePlayerPeers();
    this._updateNowPlaying();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (!this._refreshTimer) {
      this._refreshTimer = setInterval(() => {
        this._refreshTick += 1;
        if (this._view === "now") this._updateNowPlaying();
        if (this._refreshTick % 3 === 0) {
          if (this._view === "favorites") this._loadFavorites(false);
          else if (this._view === "recents") this._loadRecents(false);
        }
      }, 1000);
    }
  }

  disconnectedCallback() {
    if (this._refreshTimer) {
      clearInterval(this._refreshTimer);
      this._refreshTimer = null;
    }
    for (const pending of this._volumeWrites.values()) {
      if (pending.timer) clearTimeout(pending.timer);
    }
    this._volumeWrites.clear();
    if (this._speechRecognition) {
      try {
        this._speechRecognition.stop();
      } catch (_error) {
        // Recognition may already be stopped.
      }
      this._speechRecognition = null;
      this._dictating = false;
    }
  }


  _escape(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  _parseUri(uri) {
    const match = /^([^:]+):\/\/[^/]+\/(.+)$/.exec(uri || "");
    return match ? { provider: match[1], itemId: match[2] } : null;
  }

  _providerLabel(uri = "") {
    const provider = uri.split("://")[0];
    if (provider.startsWith("apple_music")) return "Apple Music";
    if (provider.startsWith("spotify")) return "Spotify";
    return "Library";
  }

  _artistName(item) {
    return (item?.artists || []).map((artist) => artist.name).filter(Boolean).join(", ");
  }

  async _ws(type, payload = {}) {
    if (!this._hass) throw new Error("Home Assistant is not available");
    return this._hass.connection.sendMessagePromise({
      type,
      config_entry_id: this._config.config_entry_id,
      ...payload,
    });
  }

  _isSelectedGroup() {
    return this._hass?.states?.[this._selectedPlayer]?.attributes?.mass_player_type === "group";
  }

  _selectedPlayerName() {
    return (
      this._hass?.states?.[this._selectedPlayer]?.attributes?.friendly_name ||
      this._selectedPlayer ||
      "Choose speaker"
    );
  }

  async _loadNativePlayerPeers() {
    if (
      this._nativePeerLoadInFlight ||
      !this._hass ||
      !this._config?.config_entry_id ||
      !Array.isArray(this._config?.players)
    ) {
      return;
    }
    this._nativePeerLoadInFlight = true;
    try {
      const peers = await this._ws("family_music/player_peers", {
        player_entity_ids: this._config.players,
      });
      this._nativePlayerPeers =
        peers && typeof peers === "object" && !Array.isArray(peers) ? peers : {};
      if (this._destinationOpen) this._renderNowOverlays();
    } catch (_error) {
      this._nativePlayerPeers = {};
    } finally {
      this._nativePeerLoadInFlight = false;
    }
  }

  _destinationIsPlaying(entityId) {
    const maPlaying = this._hass?.states?.[entityId]?.state === "playing";
    const nativeEntityId = this._nativePlayerPeers?.[entityId];
    const nativePlaying = nativeEntityId
      ? this._hass?.states?.[nativeEntityId]?.state === "playing"
      : false;
    return maPlaying || nativePlaying;
  }

  _renderDestinationMenu() {
    if (!this._destinationOpen) return "";
    const players = (this._config?.players || [])
      .filter((entityId) => this._hass?.states?.[entityId])
      .map((entityId) => {
        const playerState = this._hass.states[entityId];
        const name = playerState?.attributes?.friendly_name || entityId;
        const selected = entityId === this._selectedPlayer;
        const playing = this._destinationIsPlaying(entityId);
        const activity = playing
          ? `<span class="playing-equalizer" title="Playing" aria-label="Playing">
              <i></i><i></i><i></i>
            </span>`
          : '<span class="destination-activity" aria-hidden="true"></span>';
        return `
          <button class="destination-option ${selected ? "selected" : ""}" data-destination="${this._escape(entityId)}">
            <ha-icon icon="${selected ? "mdi:check-circle" : "mdi:speaker"}"></ha-icon>
            <span>${this._escape(name)}</span>
            ${activity}
          </button>
        `;
      })
      .join("");
    return `
      <div class="now-popover destination-popover">
        <div class="popover-title">Play in</div>
        <div class="destination-list">${players}</div>
      </div>
    `;
  }

  _renderMoreMenu() {
    if (!this._moreOpen) return "";
    const groupSection = this._isSelectedGroup()
      ? `
        <div class="popover-title">Speaker Balance</div>
        <div class="balance-help">Shape where the sound comes from. No percentages to manage.</div>
        <div class="member-balance-list">
          ${this._groupMembersLoading
            ? '<div class="menu-status">Loading speakers…</div>'
            : this._groupMembers.length
              ? this._groupMembers.map((member) => {
                  const trim = this._memberTrimValues.get(member.entity_id) ?? 0;
                  return `
                    <div class="member-balance-row" data-member="${this._escape(member.entity_id)}">
                      <div class="member-balance-name">${this._escape(member.name)}</div>
                      <input class="member-trim" data-entity="${this._escape(member.entity_id)}"
                        type="range" min="-12" max="12" step="1" value="${trim}"
                        aria-label="${this._escape(member.name)} balance, less to more">
                    </div>
                  `;
                }).join("")
              : '<div class="menu-status">No group members found.</div>'}
        </div>
        ${this._groupMembers.length
          ? '<div class="balance-scale"><span>Less</span><span>Neutral</span><span>More</span></div>'
          : ""}
        ${this._groupMembers.length
          ? '<button id="resetSpeakerBalance" class="balance-reset" type="button">Reset balance</button>'
          : ""}
      `
      : '<div class="menu-status">This destination is a single speaker.</div>';

    return `
      <div class="now-popover more-popover">
        ${groupSection}
      </div>
    `;
  }

  _renderNowOverlays() {
    const overlay = this.shadowRoot.getElementById("nowOverlay");
    if (!overlay) return;
    const anyOpen = this._destinationOpen || this._moreOpen;
    overlay.classList.toggle("open", anyOpen);
    overlay.innerHTML = anyOpen
      ? `<button id="overlayScrim" class="overlay-scrim" aria-label="Close"></button>
         ${this._renderDestinationMenu()}
         ${this._renderMoreMenu()}`
      : "";
    this._wireNowOverlays();
  }

  _wireNowOverlays() {
    this.shadowRoot.getElementById("overlayScrim")?.addEventListener("click", () => {
      this._destinationOpen = false;
      this._moreOpen = false;
      this._renderNowOverlays();
    });
    this.shadowRoot.querySelectorAll("[data-destination]").forEach((button) => {
      button.addEventListener("click", () => {
        const entityId = button.dataset.destination;
        if (!entityId || entityId === this._selectedPlayer) {
          this._destinationOpen = false;
          this._renderNowOverlays();
          return;
        }
        this._selectedPlayer = entityId;
        this._groupMembers = [];
        this._groupMembersFor = null;
        this._membersExpanded = false;
        this._mixBaselinePercent = null;
        this._memberTrimValues.clear();
        this._destinationOpen = false;
        this._moreOpen = false;
        this._optimisticPlayback = null;
        this._optimisticPlaybackUntil = 0;
        this._optimisticShuffle = null;
        this._optimisticShuffleUntil = 0;
        this._optimisticRepeat = null;
        this._optimisticRepeatUntil = 0;
        this._optimisticMute = null;
        this._optimisticMuteUntil = 0;
        localStorage.setItem("family-music-card-player", entityId);
        this._render();
      });
    });
    this._wireMemberVolumePanel();
  }

  async _loadGroupMembers(force = false) {
    if (!this._selectedPlayer || !this._hass || !this._isSelectedGroup()) {
      this._groupMembers = [];
      this._groupMembersFor = this._selectedPlayer;
      this._groupMembersLoading = false;
      return;
    }
    if (!force && this._groupMembersFor === this._selectedPlayer) return;
    if (this._groupMembersLoading) return;

    const player = this._selectedPlayer;
    this._groupMembersLoading = true;
    if (this._moreOpen) this._renderNowOverlays();
    try {
      const members = await this._ws("family_music/group_members", {
        player_entity_id: player,
      });
      if (this._selectedPlayer === player) {
        this._groupMembers = Array.isArray(members) ? members : [];
        this._groupMembersFor = player;
        this._initializeSpeakerBalance();
      }
    } catch (_error) {
      if (this._selectedPlayer === player) {
        this._groupMembers = [];
        this._groupMembersFor = player;
      }
    } finally {
      this._groupMembersLoading = false;
      if (this._view === "now" && this._selectedPlayer === player && this._moreOpen) {
        this._renderNowOverlays();
      }
    }
  }

  _initializeSpeakerBalance() {
    const samples = this._groupMembers
      .map((member) => {
        const volume = this._hass?.states?.[member.entity_id]?.attributes?.volume_level;
        return volume == null ? null : Math.round(volume * 100);
      })
      .filter((value) => Number.isFinite(value));
    if (!samples.length) {
      this._mixBaselinePercent = 0;
      this._memberTrimValues.clear();
      return;
    }
    this._mixBaselinePercent = samples.reduce((sum, value) => sum + value, 0) / samples.length;
    this._memberTrimValues.clear();
    this._groupMembers.forEach((member) => {
      const volume = this._hass?.states?.[member.entity_id]?.attributes?.volume_level;
      const percent = volume == null ? this._mixBaselinePercent : Math.round(volume * 100);
      const trim = Math.max(-12, Math.min(12, Math.round((percent - this._mixBaselinePercent) / 2)));
      this._memberTrimValues.set(member.entity_id, trim);
    });
  }

  _trimTargetPercent(trim) {
    const baseline = Number.isFinite(this._mixBaselinePercent) ? this._mixBaselinePercent : 0;
    return Math.max(0, Math.min(100, Math.round(baseline + Number(trim || 0) * 2)));
  }

  _resetSpeakerBalance() {
    this._memberTrimValues.clear();
    this._groupMembers.forEach((member) => this._memberTrimValues.set(member.entity_id, 0));
    const baseline = Math.max(0, Math.min(100, Math.round(this._mixBaselinePercent || 0)));
    this._groupMembers.forEach((member) => {
      this._queueVolumeWrite(
        member.entity_id,
        baseline,
        true,
        member.native_entity_id || member.entity_id
      );
    });
    this._renderNowOverlays();
  }

  _memberVolumeMarkup() {
    if (!this._isSelectedGroup()) return "";
    return "";
  }

  _renderMemberVolumePanel() {
    const host = this.shadowRoot.getElementById("memberVolumeHost");
    if (!host) return;
    host.innerHTML = this._memberVolumeMarkup();
    this._wireMemberVolumePanel();
  }

  _wireMemberVolumePanel() {
    this.shadowRoot.querySelectorAll(".member-trim").forEach((slider) => {
      const apply = (event, flush) => {
        const entityId = event.target.dataset.entity;
        if (!entityId) return;
        const trim = Math.max(-12, Math.min(12, Number(event.target.value || 0)));
        this._memberTrimValues.set(entityId, trim);
        const member = this._groupMembers.find((item) => item.entity_id === entityId);
        const targetPercent = this._trimTargetPercent(trim);
        this._queueVolumeWrite(
          entityId,
          targetPercent,
          flush,
          member?.native_entity_id || entityId
        );
      };
      slider.addEventListener("input", (event) => apply(event, false));
      slider.addEventListener("change", (event) => apply(event, true));
    });
    this.shadowRoot.getElementById("resetSpeakerBalance")?.addEventListener("click", () => {
      this._resetSpeakerBalance();
    });
  }

  async _search() {
    const input = this.shadowRoot.getElementById("musicSearch");
    const query = (input?.value || "").trim();
    if (!query) return;
    this._query = query;
    this._busy = true;
    this._searchResults = null;
    this._renderBrowserBody();
    try {
      this._searchResults = await this._ws("family_music/search", {
        query,
        provider: this._provider,
        limit: 50,
      });
    } catch (error) {
      this._searchResults = { error: error?.message || String(error) };
    } finally {
      this._busy = false;
      this._renderBrowserBody();
    }
  }

  async _openArtist(item) {
    const parsed = this._parseUri(item.uri);
    if (!parsed) return;
    this._busy = true;
    this._artistReturnView = this._view;
    this._selectedArtist = item;
    this._selectedAlbum = null;
    this._albumTracks = [];
    this._view = "artist";
    this._render();
    try {
      const albums = await this._ws("family_music/artist_albums", {
        provider: parsed.provider,
        item_id: parsed.itemId,
      });
      this._selectedArtist = { ...item, albums: Array.isArray(albums) ? albums : [] };
    } catch (error) {
      this._selectedArtist = { ...item, albums: [], error: error?.message || String(error) };
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _openAlbum(item) {
    const parsed = this._parseUri(item.uri);
    if (!parsed) return;
    this._busy = true;
    this._albumReturnView = this._view;
    this._selectedAlbum = item;
    this._albumTracks = [];
    this._view = "album";
    this._render();
    try {
      const tracks = await this._ws("family_music/album_tracks", {
        provider: parsed.provider,
        item_id: parsed.itemId,
      });
      this._albumTracks = Array.isArray(tracks) ? tracks : [];
    } catch (error) {
      this._albumTracks = [];
      this._selectedAlbum = { ...item, error: error?.message || String(error) };
    } finally {
      this._busy = false;
      this._render();
    }
  }

  async _play(item, enqueue = "play") {
    if (!this._selectedPlayer || !item?.uri) return;
    await this._hass.callService("music_assistant", "play_media", {
      entity_id: this._selectedPlayer,
      media_id: item.uri,
      media_type: item.media_type || "track",
      enqueue,
    });
    this._view = "now";
    this._render();
  }

  async _playAlbum(shuffle = false) {
    if (!this._selectedAlbum) return;
    if (shuffle && this._selectedPlayer) {
      await this._hass.callService("media_player", "shuffle_set", {
        entity_id: this._selectedPlayer,
        shuffle: true,
      });
    }
    await this._play(this._selectedAlbum, "play");
  }

  _queueId() {
    return this._hass?.states?.[this._selectedPlayer]?.attributes?.active_queue || null;
  }

  async _openRecents() {
    this._view = "recents";
    this._recents = [];
    this._render();
    await this._loadRecents(true);
  }

  async _loadRecents(showBusy = false) {
    if (this._refreshInFlight) return;
    const queueId = this._queueId();
    if (!queueId) {
      this._recents = [];
      this._busy = false;
      this._render();
      return;
    }
    this._refreshInFlight = true;
    if (showBusy) {
      this._busy = true;
      this._renderBrowserBody();
    }
    try {
      const items = await this._ws("family_music/recents", {
        queue_id: queueId,
        limit: 40,
      });
      this._recents = Array.isArray(items) ? items : [];
    } catch (error) {
      this._recents = [{ error: error?.message || String(error) }];
    } finally {
      this._refreshInFlight = false;
      this._busy = false;
      if (this._view === "recents") this._renderBrowserBody();
    }
  }

  async _openFavorites() {
    this._view = "favorites";
    this._render();
    await this._loadFavorites(true);
  }

  async _loadFavorites(showBusy = false) {
    if (this._refreshInFlight) return;
    this._refreshInFlight = true;
    if (showBusy) {
      this._busy = true;
      this._renderBrowserBody();
    }
    try {
      this._favorites = await this._ws("family_music/favorites", { limit: 40 });
    } catch (error) {
      this._favorites = { error: error?.message || String(error) };
    } finally {
      this._refreshInFlight = false;
      this._busy = false;
      if (this._view === "favorites") this._renderBrowserBody();
    }
  }

  _setView(view) {
    this._view = view;
    this._render();
  }

  _render() {
    if (!this._config) return;
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <ha-card class="card">
        ${this._view === "now" ? this._renderNowView() : this._renderBrowserView()}
      </ha-card>
    `;
    this._wire();
    this._syncPlayerSelector();
    this._updateNowPlaying();
  }

  _renderNowView() {
    const playerName = this._selectedPlayerName();
    return `
      <div class="now-shell">
        <div id="hero" class="hero">
          <img id="bgImage" class="hero-bg" alt="">
          <div class="hero-shade"></div>
          <div class="now-content">
            <div class="artwork-stage">
              <div class="artwork-placeholder"><ha-icon icon="mdi:music"></ha-icon></div>
              <img id="artImage" class="now-artwork" alt="">
            </div>

            <div class="track-copy-main">
              <div id="trackTitle" class="track-title">Nothing playing</div>
              <div id="trackMeta" class="track-meta">—</div>
            </div>

            <div class="progress-wrap">
              <input id="trackProgress" class="track-progress" type="range" min="0" max="100" step="0.1" value="0" aria-label="Track progress">
              <div class="progress-time"><span id="elapsed">0:00</span><span id="remaining">-0:00</span></div>
            </div>

            <div class="transport">
              <button id="shuffleToggle" class="mode-button" aria-label="Shuffle" title="Shuffle">
                <ha-icon icon="mdi:shuffle"></ha-icon>
              </button>
              <button id="prev" class="transport-button" aria-label="Previous"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
              <button id="playPause" class="play-button" aria-label="Play or pause"><ha-icon icon="mdi:play"></ha-icon></button>
              <button id="next" class="transport-button" aria-label="Next"><ha-icon icon="mdi:skip-next"></ha-icon></button>
              <button id="repeatToggle" class="mode-button" aria-label="Repeat" title="Repeat off">
                <ha-icon icon="mdi:repeat"></ha-icon>
              </button>
            </div>

            <div class="volume-row">
              <button id="muteToggle" class="mute-toggle" title="Mute" aria-label="Mute">
                <ha-icon icon="mdi:volume-high"></ha-icon>
              </button>
              <button id="volumeDown" class="volume-nudge" title="Volume down 1" aria-label="Volume down 1">−</button>
              <input id="volume" class="volume" type="range" min="0" max="100" step="1" value="20">
              <span id="volumeValue" class="volume-value">20</span>
              <button id="volumeUp" class="volume-nudge" title="Volume up 1" aria-label="Volume up 1">+</button>
            </div>

            <div class="bottom-actions">
              <button id="openSearch" class="bottom-circle music-search-button" title="Search music" aria-label="Search music">
                <svg class="music-search-icon" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
                  <defs>
                    <linearGradient id="musicSearchGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stop-color="#ff3f82"></stop>
                      <stop offset="55%" stop-color="#ff1550"></stop>
                      <stop offset="100%" stop-color="#ef0032"></stop>
                    </linearGradient>
                  </defs>
                  <circle class="music-search-tile" cx="32" cy="32" r="31" fill="url(#musicSearchGradient)"></circle>
                  <circle cx="27" cy="27" r="12" fill="none" stroke="#fff" stroke-width="6"></circle>
                  <path d="M36 36l12 12" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round"></path>
                </svg>
              </button>
              <button id="destinationButton" class="destination-pill" title="Change playback destination">
                <ha-icon icon="mdi:speaker"></ha-icon>
                <span id="destinationName">${this._escape(playerName)}</span>
                <ha-icon class="destination-chevron" icon="mdi:chevron-up"></ha-icon>
              </button>
              <button id="moreButton" class="bottom-circle group-speakers-button" title="Group speaker controls" aria-label="Group speaker controls">
                <ha-icon icon="mdi:speaker-multiple"></ha-icon>
              </button>
            </div>
          </div>
          <div id="nowOverlay" class="now-overlay"></div>
        </div>
      </div>
    `;
  }

  _renderBrowserView() {
    const playerName =
      this._hass?.states?.[this._selectedPlayer]?.attributes?.friendly_name || "this zone";
    const title =
      this._view === "artist"
        ? this._selectedArtist?.name || "Artist"
        : this._view === "album"
          ? this._selectedAlbum?.name || "Album"
          : this._view === "recents"
            ? `Recent · ${playerName}`
            : this._view === "favorites"
              ? "Favorites"
              : "Choose Music";

    return `
      <div class="browser-shell">
        <div class="browser-header">
          <button id="browserBack" class="circle-button"><ha-icon icon="mdi:arrow-left"></ha-icon></button>
          <div class="browser-title">${this._escape(title)}</div>
          <div class="header-actions">
            ${["favorites", "recents"].includes(this._view) ? '<button id="refreshView" class="circle-button" title="Refresh"><ha-icon icon="mdi:refresh"></ha-icon></button>' : ""}
            <button id="browserClose" class="circle-button"><ha-icon icon="mdi:close"></ha-icon></button>
          </div>
        </div>
        ${this._view === "search" ? this._renderSearchControls() : ""}
        <div id="browserBody" class="browser-body">${this._renderBrowserBodyMarkup()}</div>
        ${this._renderNavStrip(this._view)}
      </div>
    `;
  }

  _renderNavStrip(activeView = this._view) {
    const active = ["artist", "album"].includes(activeView) ? "" : activeView;
    return `
      <div class="nav-strip">
        <button id="navNow" class="nav-button ${active === "now" ? "active" : ""}" title="Now Playing">
          <ha-icon icon="mdi:home"></ha-icon><span>Now</span>
        </button>
        <button id="navRecents" class="nav-button ${active === "recents" ? "active" : ""}" title="Recent for this zone">
          <ha-icon icon="mdi:history"></ha-icon><span>Recents</span>
        </button>
        <button id="navFavorites" class="nav-button ${active === "favorites" ? "active" : ""}" title="Favorites">
          <ha-icon icon="mdi:heart"></ha-icon><span>Favorites</span>
        </button>
        <button id="navSearch" class="nav-button ${active === "search" ? "active" : ""}" title="Search">
          <ha-icon icon="mdi:magnify"></ha-icon><span>Search</span>
        </button>
      </div>
    `;
  }

  _renderSearchControls() {
    return `
      <div class="provider-row">
        <button class="provider-chip ${this._provider === "apple" ? "active" : ""}" data-provider="apple">Apple Music</button>
        <button class="provider-chip ${this._provider === "spotify" ? "active" : ""}" data-provider="spotify">Spotify</button>
        <button class="provider-chip ${this._provider === "all" ? "active" : ""}" data-provider="all">All</button>
      </div>
      <div class="search-row">
        <input id="musicSearch" class="search-input" type="search"
          placeholder="Artist, album, song or playlist…" value="${this._escape(this._query)}">
        <button id="voiceSearch" class="voice-search" type="button" title="Dictate search" aria-label="Dictate search">
          <ha-icon icon="mdi:microphone"></ha-icon>
        </button>
        <button id="searchGo" class="search-go">Search</button>
      </div>
    `;
  }

  _renderBrowserBody() {
    const body = this.shadowRoot.getElementById("browserBody");
    if (body) body.innerHTML = this._renderBrowserBodyMarkup();
    this._wireBrowserItems();
  }

  _renderBrowserBodyMarkup() {
    if (this._busy) {
      return '<div class="status">Loading…</div>';
    }

    if (this._view === "artist") {
      if (this._selectedArtist?.error) {
        return `<div class="status error">${this._escape(this._selectedArtist.error)}</div>`;
      }
      const albums = this._selectedArtist?.albums || [];
      return albums.length
        ? this._section("Albums", albums.map((item) => this._albumTile(item)).join(""))
        : '<div class="status">No albums found.</div>';
    }

    if (this._view === "album") {
      if (this._selectedAlbum?.error) {
        return `<div class="status error">${this._escape(this._selectedAlbum.error)}</div>`;
      }
      const album = this._selectedAlbum;
      const tracks = this._albumTracks;
      return `
        <div class="album-hero">
          <div class="album-art">${album?.image ? `<img src="${this._escape(album.image)}">` : '<ha-icon icon="mdi:album"></ha-icon>'}</div>
          <div class="album-info">
            <div class="album-name">${this._escape(album?.name || "")}</div>
            <div class="album-artist">${this._escape(this._artistName(album))}</div>
            <div class="album-actions">
              <button id="playAlbum" class="primary-action"><ha-icon icon="mdi:play"></ha-icon> Play Album</button>
              <button id="shuffleAlbum" class="secondary-action"><ha-icon icon="mdi:shuffle"></ha-icon> Shuffle</button>
            </div>
          </div>
        </div>
        ${tracks.length
          ? this._section("Tracks", tracks.map((item, index) => this._trackRow(item, index + 1)).join(""), "track-list")
          : '<div class="status">No tracks found.</div>'}
      `;
    }

    if (this._view === "recents") {
      if (this._recents[0]?.error) {
        return `<div class="status error">${this._escape(this._recents[0].error)}</div>`;
      }
      if (!this._queueId()) {
        return '<div class="status">No Music Assistant queue is available for this zone yet.</div>';
      }
      return this._recents.length
        ? this._section("Recently Played", this._recents.map((item) => this._recentRow(item)).join(""), "recent-list")
        : '<div class="status">Nothing has been played recently in this zone.</div>';
    }

    if (this._view === "favorites") {
      if (this._favorites?.error) {
        return `<div class="status error">${this._escape(this._favorites.error)}</div>`;
      }
      if (!this._favorites) {
        return '<div class="status">Loading favorites…</div>';
      }
      const artists = this._favorites.artists || [];
      const albums = this._favorites.albums || [];
      const tracks = this._favorites.tracks || [];
      const playlists = this._favorites.playlists || [];
      const radio = this._favorites.radio || [];
      const markup = [
        this._section("Artists", artists.map((item) => this._artistTile(item)).join("")),
        this._section("Albums", albums.map((item) => this._albumTile(item)).join("")),
        this._section("Tracks", tracks.map((item, index) => this._trackRow(item, index + 1)).join(""), "track-list"),
        this._section("Playlists", playlists.map((item) => this._albumTile(item)).join("")),
        this._section("Radio", radio.map((item) => this._albumTile(item)).join("")),
      ].join("");
      return markup || '<div class="status">No favorites found in Music Assistant.</div>';
    }

    if (!this._searchResults) {
      return '<div class="status">Search for an artist, album, song, playlist or station.</div>';
    }
    if (this._searchResults.error) {
      return `<div class="status error">${this._escape(this._searchResults.error)}</div>`;
    }

    const artists = this._searchResults.artists || [];
    const albums = this._searchResults.albums || [];
    const tracks = this._searchResults.tracks || [];
    const playlists = this._searchResults.playlists || [];
    const radio = this._searchResults.radio || [];

    const markup = [
      this._section("Artists", artists.map((item) => this._artistTile(item)).join("")),
      this._section("Albums", albums.map((item) => this._albumTile(item)).join("")),
      this._section("Tracks", tracks.map((item, index) => this._trackRow(item, index + 1)).join(""), "track-list"),
      this._section("Playlists", playlists.map((item) => this._albumTile(item)).join("")),
      this._section("Radio", radio.map((item) => this._albumTile(item)).join("")),
    ].join("");

    return markup || '<div class="status">No matching results.</div>';
  }

  _section(title, content, className = "tile-grid") {
    if (!content) return "";
    return `<section class="section"><h3>${this._escape(title)}</h3><div class="${className}">${content}</div></section>`;
  }

  _artistTile(item) {
    return `
      <button class="media-tile artist-item" data-uri="${this._escape(item.uri)}">
        <div class="artwork round">
          ${item.image ? `<img src="${this._escape(item.image)}">` : '<ha-icon icon="mdi:account-music"></ha-icon>'}
          <span class="source-tag">${this._escape(this._providerLabel(item.uri))}</span>
        </div>
        <div class="tile-title">${this._escape(item.name)}</div>
      </button>
    `;
  }

  _albumTile(item) {
    return `
      <button class="media-tile album-item" data-uri="${this._escape(item.uri)}">
        <div class="artwork">
          ${item.image ? `<img src="${this._escape(item.image)}">` : '<ha-icon icon="mdi:album"></ha-icon>'}
          <span class="source-tag">${this._escape(this._providerLabel(item.uri))}</span>
        </div>
        <div class="tile-title">${this._escape(item.name)}</div>
        <div class="tile-subtitle">${this._escape(this._artistName(item) || item.version || "")}</div>
      </button>
    `;
  }

  _recentRow(item) {
    const subtitle = [this._artistName(item), item.album?.name].filter(Boolean).join(" · ");
    const typeLabel = String(item.media_type || "music")
      .replaceAll("_", " ")
      .replace(/^./, (value) => value.toUpperCase());
    return `
      <button class="recent-row recent-item" data-uri="${this._escape(item.uri)}">
        <div class="recent-thumb">${item.image ? `<img src="${this._escape(item.image)}">` : '<ha-icon icon="mdi:music"></ha-icon>'}</div>
        <div class="recent-copy">
          <div class="recent-title">${this._escape(item.name)}</div>
          <div class="recent-subtitle">${this._escape(subtitle || this._providerLabel(item.uri))}</div>
        </div>
        <span class="recent-type">${this._escape(typeLabel)}</span>
      </button>
    `;
  }

  _trackRow(item, index) {
    return `
      <button class="track-row track-item" data-uri="${this._escape(item.uri)}">
        <div class="track-index">${index}</div>
        <div class="track-thumb">${item.image ? `<img src="${this._escape(item.image)}">` : '<ha-icon icon="mdi:music-note"></ha-icon>'}</div>
        <div class="track-copy">
          <div class="track-row-title">${this._escape(item.name)}</div>
          <div class="track-row-subtitle">${this._escape([this._artistName(item), item.album?.name].filter(Boolean).join(" · "))}</div>
        </div>
        <span class="track-source">${this._escape(this._providerLabel(item.uri))}</span>
      </button>
    `;
  }

  _formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const whole = Math.floor(seconds);
    const minutes = Math.floor(whole / 60);
    const remainder = whole % 60;
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  }

  _estimatedPosition(state, attrs) {
    const duration = Number(attrs.media_duration);
    let position = Number(attrs.media_position);
    if (!Number.isFinite(position)) position = 0;
    if (state.state === "playing" && attrs.media_position_updated_at) {
      const updatedAt = Date.parse(attrs.media_position_updated_at);
      if (Number.isFinite(updatedAt)) {
        position += Math.max(0, (Date.now() - updatedAt) / 1000);
      }
    }
    if (Number.isFinite(duration) && duration > 0) {
      position = Math.min(position, duration);
    }
    return Math.max(0, position);
  }

  _setOptimisticVolume(entityId, percent) {
    const bounded = Math.max(0, Math.min(100, Math.round(percent)));
    this._optimisticVolumes.set(entityId, {
      value: bounded,
      until: Date.now() + 5000,
    });
    return bounded;
  }

  _queueVolumeWrite(entityId, percent, flush = false, targetEntityId = entityId) {
    if (!entityId || !targetEntityId || !this._hass) return;
    const bounded = this._setOptimisticVolume(entityId, percent);
    let pending = this._volumeWrites.get(entityId);
    if (!pending) {
      pending = { timer: null, lastSent: 0, pendingValue: null, targetEntityId };
      this._volumeWrites.set(entityId, pending);
    }
    pending.pendingValue = bounded;
    pending.targetEntityId = targetEntityId;

    const send = () => {
      if (pending.timer) clearTimeout(pending.timer);
      pending.timer = null;
      const value = pending.pendingValue;
      const target = pending.targetEntityId;
      pending.pendingValue = null;
      pending.lastSent = Date.now();
      this._hass.callService("media_player", "volume_set", {
        entity_id: target,
        volume_level: value / 100,
      }).catch(() => {
        this._optimisticVolumes.delete(entityId);
        this._updateNowPlaying();
      });
    };

    const elapsed = Date.now() - pending.lastSent;
    if (flush || pending.lastSent === 0 || elapsed >= 35) {
      send();
      return;
    }
    if (!pending.timer) {
      pending.timer = setTimeout(send, Math.max(0, 35 - elapsed));
    }
  }

  _optimisticVolume(entityId, actualPercent) {
    const pending = this._optimisticVolumes.get(entityId);
    if (!pending) return actualPercent;
    if (actualPercent === pending.value) {
      this._optimisticVolumes.delete(entityId);
      return actualPercent;
    }
    if (Date.now() >= pending.until) {
      this._optimisticVolumes.delete(entityId);
      return actualPercent;
    }
    return pending.value;
  }

  _setVolume(percent, flush = false) {
    if (!this._selectedPlayer) return;
    const bounded = this._setOptimisticVolume(this._selectedPlayer, percent);
    const slider = this.shadowRoot.getElementById("volume");
    if (slider) slider.value = bounded;
    const display = this.shadowRoot.getElementById("volumeValue");
    if (display) display.textContent = String(bounded);
    const target = this._nativePlayerPeers?.[this._selectedPlayer] || this._selectedPlayer;
    this._queueVolumeWrite(this._selectedPlayer, bounded, flush, target);
  }

  _wire() {
    const playerSelect = this.shadowRoot.getElementById("playerSelect");
    playerSelect?.addEventListener("change", (event) => {
      this._selectedPlayer = event.target.value;
      this._groupMembers = [];
      this._groupMembersFor = null;
      this._membersExpanded = false;
      this._mixBaselinePercent = null;
      this._memberTrimValues.clear();
      this._optimisticPlayback = null;
      this._optimisticPlaybackUntil = 0;
      this._optimisticShuffle = null;
      this._optimisticShuffleUntil = 0;
      this._optimisticRepeat = null;
      this._optimisticRepeatUntil = 0;
      this._optimisticMute = null;
      this._optimisticMuteUntil = 0;
      localStorage.setItem("family-music-card-player", this._selectedPlayer);
      this._updateNowPlaying();
      this._loadGroupMembers(false);
    });

    const openSearch = () => {
      this._view = "search";
      this._render();
      setTimeout(() => this.shadowRoot.getElementById("musicSearch")?.focus(), 20);
    };
    this.shadowRoot.getElementById("openSearch")?.addEventListener("click", openSearch);
    this.shadowRoot.getElementById("navSearch")?.addEventListener("click", openSearch);
    this.shadowRoot.getElementById("destinationButton")?.addEventListener("click", () => {
      this._destinationOpen = !this._destinationOpen;
      this._moreOpen = false;
      this._renderNowOverlays();
    });
    this.shadowRoot.getElementById("moreButton")?.addEventListener("click", async () => {
      if (!this._isSelectedGroup()) return;
      this._moreOpen = !this._moreOpen;
      this._destinationOpen = false;
      this._renderNowOverlays();
      if (this._moreOpen) {
        this._mixBaselinePercent = null;
        this._memberTrimValues.clear();
        await this._loadGroupMembers(true);
      }
    });
    this.shadowRoot.getElementById("navNow")?.addEventListener("click", () => this._setView("now"));
    this.shadowRoot.getElementById("navRecents")?.addEventListener("click", () => this._openRecents());
    this.shadowRoot.getElementById("navFavorites")?.addEventListener("click", () => this._openFavorites());

    this.shadowRoot.getElementById("shuffleToggle")?.addEventListener("click", () => {
      if (!this._selectedPlayer) return;
      const attrs = this._hass?.states?.[this._selectedPlayer]?.attributes || {};
      const effective =
        this._optimisticShuffle !== null && Date.now() < this._optimisticShuffleUntil
          ? this._optimisticShuffle
          : Boolean(attrs.shuffle);
      const next = !effective;
      this._optimisticShuffle = next;
      this._optimisticShuffleUntil = Date.now() + 2500;
      const button = this.shadowRoot.getElementById("shuffleToggle");
      button?.classList.toggle("active", next);
      this._hass.callService("media_player", "shuffle_set", {
        entity_id: this._selectedPlayer,
        shuffle: next,
      }).catch(() => {
        this._optimisticShuffle = null;
        this._optimisticShuffleUntil = 0;
        this._updateNowPlaying();
      });
    });

    this.shadowRoot.getElementById("repeatToggle")?.addEventListener("click", () => {
      if (!this._selectedPlayer) return;
      const attrs = this._hass?.states?.[this._selectedPlayer]?.attributes || {};
      const effective =
        this._optimisticRepeat !== null && Date.now() < this._optimisticRepeatUntil
          ? this._optimisticRepeat
          : attrs.repeat || "off";
      const next = effective === "off" ? "all" : effective === "all" ? "one" : "off";
      this._optimisticRepeat = next;
      this._optimisticRepeatUntil = Date.now() + 2500;
      const button = this.shadowRoot.getElementById("repeatToggle");
      if (button) {
        button.classList.toggle("active", next !== "off");
        button.title = next === "one" ? "Repeat one" : next === "all" ? "Repeat all" : "Repeat off";
        button.setAttribute("aria-label", button.title);
        button.innerHTML = `<ha-icon icon="${next === "one" ? "mdi:repeat-once" : "mdi:repeat"}"></ha-icon>`;
      }
      this._hass.callService("media_player", "repeat_set", {
        entity_id: this._selectedPlayer,
        repeat: next,
      }).catch(() => {
        this._optimisticRepeat = null;
        this._optimisticRepeatUntil = 0;
        this._updateNowPlaying();
      });
    });

    this.shadowRoot.getElementById("prev")?.addEventListener("click", () => {
      if (this._selectedPlayer) {
        this._hass.callService("media_player", "media_previous_track", { entity_id: this._selectedPlayer });
      }
    });
    this.shadowRoot.getElementById("next")?.addEventListener("click", () => {
      if (this._selectedPlayer) {
        this._hass.callService("media_player", "media_next_track", { entity_id: this._selectedPlayer });
      }
    });
    this.shadowRoot.getElementById("playPause")?.addEventListener("click", () => {
      if (!this._selectedPlayer) return;
      const actualState = this._hass?.states?.[this._selectedPlayer]?.state;
      const effectiveState =
        this._optimisticPlayback && Date.now() < this._optimisticPlaybackUntil
          ? this._optimisticPlayback
          : actualState;
      const targetState = effectiveState === "playing" ? "paused" : "playing";
      this._optimisticPlayback = targetState;
      this._optimisticPlaybackUntil = Date.now() + 1800;
      const button = this.shadowRoot.getElementById("playPause");
      if (button) {
        button.innerHTML = `<ha-icon icon="${targetState === "playing" ? "mdi:pause" : "mdi:play"}"></ha-icon>`;
      }
      this._hass.callService(
        "media_player",
        targetState === "playing" ? "media_play" : "media_pause",
        { entity_id: this._selectedPlayer }
      ).catch(() => {
        this._optimisticPlayback = null;
        this._optimisticPlaybackUntil = 0;
        this._updateNowPlaying();
      });
    });
    const progress = this.shadowRoot.getElementById("trackProgress");
    progress?.addEventListener("input", (event) => {
      this._seeking = true;
      const duration = Number(progress.max || 0);
      const position = Number(event.target.value || 0);
      progress.style.setProperty(
        "--progress-pct",
        duration > 0 ? `${Math.min(100, (position / duration) * 100)}%` : "0%"
      );
      const elapsed = this.shadowRoot.getElementById("elapsed");
      const remaining = this.shadowRoot.getElementById("remaining");
      if (elapsed) elapsed.textContent = this._formatTime(position);
      if (remaining) remaining.textContent = `-${this._formatTime(Math.max(0, duration - position))}`;
    });
    progress?.addEventListener("change", async (event) => {
      if (!this._selectedPlayer) {
        this._seeking = false;
        return;
      }
      try {
        await this._hass.callService("media_player", "media_seek", {
          entity_id: this._selectedPlayer,
          seek_position: Number(event.target.value || 0),
        });
      } finally {
        this._seeking = false;
        this._updateNowPlaying();
      }
    });

    this.shadowRoot.getElementById("muteToggle")?.addEventListener("click", () => {
      if (!this._selectedPlayer) return;
      const actualMuted = Boolean(
        this._hass?.states?.[this._selectedPlayer]?.attributes?.is_volume_muted
      );
      const effectiveMuted =
        this._optimisticMute !== null && Date.now() < this._optimisticMuteUntil
          ? this._optimisticMute
          : actualMuted;
      const nextMuted = !effectiveMuted;
      this._optimisticMute = nextMuted;
      this._optimisticMuteUntil = Date.now() + 2500;
      const button = this.shadowRoot.getElementById("muteToggle");
      if (button) {
        button.title = nextMuted ? "Unmute" : "Mute";
        button.setAttribute("aria-label", button.title);
        button.innerHTML = `<ha-icon icon="${nextMuted ? "mdi:volume-off" : "mdi:volume-high"}"></ha-icon>`;
        button.classList.toggle("muted", nextMuted);
      }
      const target = this._nativePlayerPeers?.[this._selectedPlayer] || this._selectedPlayer;
      this._hass.callService("media_player", "volume_mute", {
        entity_id: target,
        is_volume_muted: nextMuted,
      }).catch(() => {
        this._optimisticMute = null;
        this._optimisticMuteUntil = 0;
        this._updateNowPlaying();
      });
    });
    this.shadowRoot.getElementById("volume")?.addEventListener("input", (event) => {
      this._setVolume(Number(event.target.value), false);
    });
    this.shadowRoot.getElementById("volume")?.addEventListener("change", (event) => {
      this._setVolume(Number(event.target.value), true);
    });
    this.shadowRoot.getElementById("volumeDown")?.addEventListener("click", () => {
      const current = Number(this.shadowRoot.getElementById("volume")?.value || 0);
      this._setVolume(current - 1, true);
    });
    this.shadowRoot.getElementById("volumeUp")?.addEventListener("click", () => {
      const current = Number(this.shadowRoot.getElementById("volume")?.value || 0);
      this._setVolume(current + 1, true);
    });

    this.shadowRoot.getElementById("browserClose")?.addEventListener("click", () => this._setView("now"));
    this.shadowRoot.getElementById("refreshView")?.addEventListener("click", () => {
      if (this._view === "favorites") this._loadFavorites(true);
      else if (this._view === "recents") this._loadRecents(true);
    });
    this.shadowRoot.getElementById("browserBack")?.addEventListener("click", () => {
      if (this._view === "album") this._setView(this._albumReturnView || "search");
      else if (this._view === "artist") this._setView(this._artistReturnView || "search");
      else this._setView("now");
    });

    this.shadowRoot.querySelectorAll("[data-provider]").forEach((button) => {
      button.addEventListener("click", () => {
        this._provider = button.dataset.provider;
        this._searchResults = null;
        this._render();
        setTimeout(() => this.shadowRoot.getElementById("musicSearch")?.focus(), 20);
      });
    });

    const runSearch = () => this._search();
    this.shadowRoot.getElementById("searchGo")?.addEventListener("click", runSearch);
    this.shadowRoot.getElementById("musicSearch")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") runSearch();
    });
    this._wireVoiceSearch();

    this.shadowRoot.getElementById("playAlbum")?.addEventListener("click", () => this._playAlbum(false));
    this.shadowRoot.getElementById("shuffleAlbum")?.addEventListener("click", () => this._playAlbum(true));

    this._wireMemberVolumePanel();
    this._wireBrowserItems();
    this._wireNowOverlays();
    this._loadGroupMembers(false);
  }

  _wireVoiceSearch() {
    const button = this.shadowRoot.getElementById("voiceSearch");
    const input = this.shadowRoot.getElementById("musicSearch");
    if (!button || !input) return;

    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Recognition) {
      button.disabled = true;
      button.title = "Dictation is not available on this device";
      button.setAttribute("aria-label", "Dictation unavailable");
      return;
    }

    button.addEventListener("click", () => {
      if (this._dictating && this._speechRecognition) {
        this._speechRecognition.stop();
        return;
      }

      const recognition = new Recognition();
      this._speechRecognition = recognition;
      recognition.lang = "en-US";
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.maxAlternatives = 1;

      const setListening = (listening) => {
        this._dictating = listening;
        button.classList.toggle("listening", listening);
        button.innerHTML = `<ha-icon icon="${listening ? "mdi:microphone" : "mdi:microphone"}"></ha-icon>`;
        button.title = listening ? "Listening…" : "Dictate search";
        button.setAttribute("aria-label", listening ? "Stop dictation" : "Dictate search");
      };

      recognition.onstart = () => setListening(true);
      recognition.onend = () => {
        setListening(false);
        if (this._speechRecognition === recognition) this._speechRecognition = null;
      };
      recognition.onerror = () => {
        setListening(false);
        if (this._speechRecognition === recognition) this._speechRecognition = null;
      };
      recognition.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript?.trim();
        if (!transcript) return;
        input.value = transcript;
        this._query = transcript;
        setTimeout(() => this._search(), 0);
      };

      try {
        recognition.start();
      } catch (_error) {
        setListening(false);
        this._speechRecognition = null;
      }
    });
  }

  _wireBrowserItems() {
    const body = this.shadowRoot.getElementById("browserBody");
    if (!body) return;

    body.querySelectorAll(".artist-item").forEach((element) => {
      element.addEventListener("click", () => {
        const artists = [
          ...(this._searchResults?.artists || []),
          ...(this._favorites?.artists || []),
        ];
        const item = artists.find((candidate) => candidate.uri === element.dataset.uri);
        if (item) this._openArtist(item);
      });
    });

    body.querySelectorAll(".album-item").forEach((element) => {
      element.addEventListener("click", () => {
        const collections = [
          ...(this._selectedArtist?.albums || []),
          ...(this._searchResults?.albums || []),
          ...(this._searchResults?.playlists || []),
          ...(this._searchResults?.radio || []),
          ...(this._favorites?.albums || []),
          ...(this._favorites?.playlists || []),
          ...(this._favorites?.radio || []),
        ];
        const item = collections.find((candidate) => candidate.uri === element.dataset.uri);
        if (!item) return;
        if (item.media_type === "album") this._openAlbum(item);
        else this._play(item);
      });
    });

    body.querySelectorAll(".track-item").forEach((element) => {
      element.addEventListener("click", () => {
        const collections = [
          ...this._albumTracks,
          ...(this._searchResults?.tracks || []),
          ...(this._favorites?.tracks || []),
        ];
        const item = collections.find((candidate) => candidate.uri === element.dataset.uri);
        if (item) this._play(item);
      });
    });

    body.querySelectorAll(".recent-item").forEach((element) => {
      element.addEventListener("click", () => {
        const item = this._recents.find((candidate) => candidate.uri === element.dataset.uri);
        if (!item) return;
        if (item.media_type === "artist") this._openArtist(item);
        else if (item.media_type === "album") this._openAlbum(item);
        else this._play(item);
      });
    });
  }

  _syncPlayerSelector() {
    const select = this.shadowRoot.getElementById("playerSelect");
    if (!select || !this._hass) return;
    const availablePlayers = this._config.players.filter((entityId) => this._hass.states[entityId]);
    select.innerHTML = availablePlayers
      .map((entityId) => {
        const name = this._hass.states[entityId]?.attributes?.friendly_name || entityId;
        return `<option value="${this._escape(entityId)}">${this._escape(name)}</option>`;
      })
      .join("");
    select.value = this._selectedPlayer || "";
  }

  _updateNowPlaying() {
    if (this._view !== "now" || !this._hass || !this._selectedPlayer) return;
    const state = this._hass.states[this._selectedPlayer];
    if (!state) return;
    const attrs = state.attributes || {};

    const title = this.shadowRoot.getElementById("trackTitle");
    const meta = this.shadowRoot.getElementById("trackMeta");
    const playPause = this.shadowRoot.getElementById("playPause");
    const volume = this.shadowRoot.getElementById("volume");
    const muteToggle = this.shadowRoot.getElementById("muteToggle");
    const progress = this.shadowRoot.getElementById("trackProgress");
    const elapsed = this.shadowRoot.getElementById("elapsed");
    const remaining = this.shadowRoot.getElementById("remaining");
    const volumeValue = this.shadowRoot.getElementById("volumeValue");
    const bgImage = this.shadowRoot.getElementById("bgImage");
    const artImage = this.shadowRoot.getElementById("artImage");
    const shuffleToggle = this.shadowRoot.getElementById("shuffleToggle");
    const repeatToggle = this.shadowRoot.getElementById("repeatToggle");
    const moreButton = this.shadowRoot.getElementById("moreButton");

    if (title) title.textContent = attrs.media_title || "Nothing playing";
    if (meta) {
      meta.textContent =
        [attrs.media_artist, attrs.media_album_name].filter(Boolean).join(" · ") || "—";
    }
    if (playPause) {
      if (
        this._optimisticPlayback &&
        (Date.now() >= this._optimisticPlaybackUntil || state.state === this._optimisticPlayback)
      ) {
        this._optimisticPlayback = null;
        this._optimisticPlaybackUntil = 0;
      }
      const playbackState =
        this._optimisticPlayback && Date.now() < this._optimisticPlaybackUntil
          ? this._optimisticPlayback
          : state.state;
      playPause.innerHTML = `<ha-icon icon="${playbackState === "playing" ? "mdi:pause" : "mdi:play"}"></ha-icon>`;
    }
    if (moreButton) {
      const grouped = this._isSelectedGroup();
      moreButton.disabled = !grouped;
      moreButton.classList.toggle("disabled", !grouped);
      moreButton.title = grouped ? "Group speaker controls" : "Single speaker — no group controls";
      moreButton.setAttribute("aria-label", moreButton.title);
      moreButton.setAttribute("aria-disabled", grouped ? "false" : "true");
      if (!grouped && this._moreOpen) {
        this._moreOpen = false;
        this._renderNowOverlays();
      }
    }

    if (shuffleToggle) {
      if (
        this._optimisticShuffle !== null &&
        (Date.now() >= this._optimisticShuffleUntil ||
          Boolean(attrs.shuffle) === this._optimisticShuffle)
      ) {
        this._optimisticShuffle = null;
        this._optimisticShuffleUntil = 0;
      }
      const shuffleState =
        this._optimisticShuffle !== null && Date.now() < this._optimisticShuffleUntil
          ? this._optimisticShuffle
          : Boolean(attrs.shuffle);
      shuffleToggle.classList.toggle("active", shuffleState);
    }
    if (repeatToggle) {
      const actualRepeat = attrs.repeat || "off";
      if (
        this._optimisticRepeat !== null &&
        (Date.now() >= this._optimisticRepeatUntil || actualRepeat === this._optimisticRepeat)
      ) {
        this._optimisticRepeat = null;
        this._optimisticRepeatUntil = 0;
      }
      const repeatState =
        this._optimisticRepeat !== null && Date.now() < this._optimisticRepeatUntil
          ? this._optimisticRepeat
          : actualRepeat;
      repeatToggle.classList.toggle("active", repeatState !== "off");
      repeatToggle.title =
        repeatState === "one" ? "Repeat one" : repeatState === "all" ? "Repeat all" : "Repeat off";
      repeatToggle.setAttribute("aria-label", repeatToggle.title);
      repeatToggle.innerHTML = `<ha-icon icon="${repeatState === "one" ? "mdi:repeat-once" : "mdi:repeat"}"></ha-icon>`;
    }

    if (volume && attrs.volume_level != null) {
      const actualPercent = Math.round(attrs.volume_level * 100);
      const displayPercent = this._optimisticVolume(this._selectedPlayer, actualPercent);
      volume.value = displayPercent;
      if (volumeValue) volumeValue.textContent = String(displayPercent);
    }
    if (muteToggle) {
      const actualMuted = Boolean(attrs.is_volume_muted);
      if (this._optimisticMute !== null) {
        if (actualMuted === this._optimisticMute || Date.now() >= this._optimisticMuteUntil) {
          this._optimisticMute = null;
          this._optimisticMuteUntil = 0;
        }
      }
      const muted =
        this._optimisticMute !== null && Date.now() < this._optimisticMuteUntil
          ? this._optimisticMute
          : actualMuted;
      muteToggle.title = muted ? "Unmute" : "Mute";
      muteToggle.setAttribute("aria-label", muted ? "Unmute" : "Mute");
      muteToggle.innerHTML = `<ha-icon icon="${muted ? "mdi:volume-off" : "mdi:volume-high"}"></ha-icon>`;
      muteToggle.classList.toggle("muted", muted);
    }

    const duration = Number(attrs.media_duration);
    const position = this._estimatedPosition(state, attrs);
    if (progress && !this._seeking) {
      if (Number.isFinite(duration) && duration > 0) {
        progress.max = duration;
        progress.value = Math.min(position, duration);
        progress.style.setProperty(
          "--progress-pct",
          `${Math.min(100, (position / duration) * 100)}%`
        );
      } else {
        progress.max = 100;
        progress.value = 0;
        progress.style.setProperty("--progress-pct", "0%");
      }
    }
    if (elapsed) elapsed.textContent = this._formatTime(position);
    if (remaining) {
      const remain = Number.isFinite(duration) && duration > 0
        ? Math.max(0, duration - position)
        : 0;
      remaining.textContent = `-${this._formatTime(remain)}`;
    }

    const picture = attrs.entity_picture_local || attrs.entity_picture;
    for (const image of [bgImage, artImage]) {
      if (!image) continue;
      if (picture) {
        if (image.getAttribute("src") !== picture) image.setAttribute("src", picture);
        image.classList.add("visible");
      } else {
        image.removeAttribute("src");
        image.classList.remove("visible");
      }
    }
    const destinationName = this.shadowRoot.getElementById("destinationName");
    if (destinationName) destinationName.textContent = this._selectedPlayerName();
  }

  _styles() {
    return `
      :host{display:block}*{box-sizing:border-box}button,input,select{font:inherit}
      .card{overflow:hidden;border-radius:22px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
      .now-shell{min-height:700px;display:flex;flex-direction:column;background:#171513}
      .hero{position:relative;min-height:700px;overflow:hidden;background:#171513;color:#fff}
      .hero-bg{position:absolute;inset:-28px;width:calc(100% + 56px);height:calc(100% + 56px);object-fit:cover;filter:blur(34px) saturate(.72);transform:scale(1.08);opacity:0;transition:opacity .25s ease}.hero-bg.visible{opacity:.38}
      .hero-shade{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(19,17,15,.56),rgba(19,17,15,.74) 54%,rgba(19,17,15,.92))}
      .now-content{position:relative;z-index:2;min-height:700px;padding:24px 24px 20px;display:flex;flex-direction:column}
      .artwork-stage{position:relative;width:min(82vw,430px);max-width:100%;aspect-ratio:1;margin:0 auto 22px;border-radius:24px;overflow:hidden;background:rgba(255,255,255,.08);box-shadow:0 16px 40px rgba(0,0,0,.28)}
      .now-artwork{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .2s ease}.now-artwork.visible{opacity:1}
      .artwork-placeholder{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:rgba(255,255,255,.28)}.artwork-placeholder ha-icon{--mdc-icon-size:76px}
      .track-copy-main{min-width:0;margin-bottom:15px}.track-title{font-size:28px;line-height:1.12;font-weight:760;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.track-meta{margin-top:7px;font-size:17px;line-height:1.25;color:rgba(255,255,255,.62);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .progress-wrap{margin-top:2px}.track-progress{width:100%;height:22px;margin:0;appearance:none;-webkit-appearance:none;background:transparent;cursor:pointer}.track-progress::-webkit-slider-runnable-track{height:4px;border-radius:999px;background:linear-gradient(to right,#fff 0 var(--progress-pct,0%),rgba(255,255,255,.34) var(--progress-pct,0%) 100%)}.track-progress::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:#fff;margin-top:-5px;box-shadow:0 0 0 1px rgba(0,0,0,.1)}.track-progress::-moz-range-track{height:4px;border-radius:999px;background:rgba(255,255,255,.34)}.track-progress::-moz-range-progress{height:4px;border-radius:999px;background:#fff}.track-progress::-moz-range-thumb{width:14px;height:14px;border:0;border-radius:50%;background:#fff}
      .progress-time{display:flex;justify-content:space-between;font-size:13px;color:rgba(255,255,255,.56);margin-top:1px}
      .transport{display:grid;grid-template-columns:44px 1fr 76px 1fr 44px;align-items:center;gap:2px;margin:15px 4px 14px}.transport-button,.play-button,.mode-button{border:0;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#fff}.transport-button{width:54px;height:54px;background:transparent;justify-self:center}.transport-button ha-icon{--mdc-icon-size:38px}.play-button{width:76px;height:76px;background:rgba(255,255,255,.10);justify-self:center;box-shadow:inset 0 0 0 1px rgba(255,255,255,.04)}.play-button ha-icon{--mdc-icon-size:42px}.mode-button{width:44px;height:44px;background:transparent;justify-self:center;color:rgba(255,255,255,.48)}.mode-button ha-icon{--mdc-icon-size:25px}.mode-button.active{color:#fff;background:rgba(255,255,255,.10)}
      .volume-row{display:grid;grid-template-columns:38px 32px minmax(0,1fr) 34px 32px;gap:7px;align-items:center;margin:2px 0 18px}.volume{width:100%;height:24px;margin:0}.volume-value{font-size:15px;text-align:center;color:rgba(255,255,255,.68);font-variant-numeric:tabular-nums}.mute-toggle,.volume-nudge{border:0;background:transparent;color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0}.mute-toggle{width:38px;height:38px;border-radius:50%}.mute-toggle ha-icon{--mdc-icon-size:25px}.mute-toggle.muted{background:rgba(255,255,255,.14)}.volume-nudge{width:32px;height:32px;font-size:31px;font-weight:300;line-height:1}
      .bottom-actions{display:grid;grid-template-columns:58px minmax(0,1fr) 58px;gap:12px;align-items:center;margin-top:auto}.bottom-circle,.destination-pill{height:58px;border:1px solid rgba(255,255,255,.26);background:rgba(255,255,255,.10);color:#fff;backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);cursor:pointer}.bottom-circle{width:58px;border-radius:50%;display:flex;align-items:center;justify-content:center}.bottom-circle ha-icon{--mdc-icon-size:29px}.music-search-button{overflow:hidden;padding:0;background:transparent}.music-search-icon{width:100%;height:100%;display:block;border-radius:50%;filter:drop-shadow(0 1px 1px rgba(0,0,0,.16))}.music-search-tile{stroke:rgba(255,255,255,.18);stroke-width:.7}.group-speakers-button ha-icon{--mdc-icon-size:31px}.group-speakers-button:disabled,.group-speakers-button.disabled{opacity:.28;cursor:default;filter:grayscale(1)}.destination-pill{min-width:0;border-radius:999px;padding:0 16px;display:grid;grid-template-columns:28px minmax(0,1fr) 20px;gap:7px;align-items:center;font-size:17px;font-weight:730}.destination-pill>span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center}.destination-pill>ha-icon{--mdc-icon-size:24px}.destination-chevron{opacity:.6}
      .now-overlay{position:absolute;inset:0;z-index:5;pointer-events:none}.now-overlay.open{pointer-events:auto}.overlay-scrim{position:absolute;inset:0;border:0;background:rgba(0,0,0,.46);backdrop-filter:blur(5px);-webkit-backdrop-filter:blur(5px)}.now-popover{position:absolute;left:18px;right:18px;bottom:18px;max-height:72%;overflow:auto;border-radius:24px;padding:18px;background:rgba(38,35,32,.96);border:1px solid rgba(255,255,255,.16);box-shadow:0 20px 60px rgba(0,0,0,.38);color:#fff}.popover-title{font-size:22px;font-weight:800;margin-bottom:12px}.popover-section-title{font-size:13px;font-weight:800;text-transform:uppercase;letter-spacing:.07em;color:rgba(255,255,255,.55);margin:2px 0 10px}
      .destination-list{display:flex;flex-direction:column;gap:5px}.destination-option{width:100%;min-height:48px;border:0;border-radius:14px;background:transparent;color:#fff;display:grid;grid-template-columns:28px minmax(0,1fr) 28px;gap:10px;align-items:center;padding:8px 10px;text-align:left;font-size:16px;cursor:pointer}.destination-option.selected{background:rgba(255,255,255,.12)}.destination-option ha-icon{--mdc-icon-size:22px}.destination-activity{width:28px;height:22px}.playing-equalizer{width:28px;height:22px;display:flex;align-items:flex-end;justify-content:center;gap:3px}.playing-equalizer i{display:block;width:3px;height:7px;border-radius:999px;background:currentColor;transform-origin:center bottom;animation:familyMusicEq 760ms ease-in-out infinite alternate}.playing-equalizer i:nth-child(2){height:15px;animation-duration:560ms;animation-delay:-210ms}.playing-equalizer i:nth-child(3){height:10px;animation-duration:690ms;animation-delay:-390ms}@keyframes familyMusicEq{0%{transform:scaleY(.35);opacity:.55}100%{transform:scaleY(1);opacity:1}}@media(prefers-reduced-motion:reduce){.playing-equalizer i{animation:none}}
      .balance-help{font-size:14px;line-height:1.35;color:rgba(255,255,255,.62);margin:-4px 0 14px}.member-balance-list{display:flex;flex-direction:column;gap:14px}.member-balance-row{display:grid;grid-template-columns:minmax(110px,1fr) minmax(150px,2fr);gap:14px;align-items:center;min-height:58px}.member-balance-name{font-size:15px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.member-trim{width:100%;height:46px;margin:0;appearance:none;-webkit-appearance:none;background:transparent;touch-action:pan-y;cursor:pointer}.member-trim::-webkit-slider-runnable-track{height:8px;border-radius:999px;background:linear-gradient(to right,rgba(255,255,255,.28),rgba(255,255,255,.28))}.member-trim::-webkit-slider-thumb{-webkit-appearance:none;width:30px;height:30px;border-radius:50%;background:#fff;margin-top:-11px;box-shadow:0 1px 6px rgba(0,0,0,.35)}.member-trim::-moz-range-track{height:8px;border-radius:999px;background:rgba(255,255,255,.28)}.member-trim::-moz-range-thumb{width:30px;height:30px;border:0;border-radius:50%;background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.35)}.balance-scale{display:grid;grid-template-columns:1fr 1fr 1fr;margin:1px 0 12px;padding-left:calc(110px + 14px);font-size:11px;color:rgba(255,255,255,.46)}.balance-scale span:nth-child(2){text-align:center}.balance-scale span:last-child{text-align:right}.balance-reset{display:block;width:100%;height:44px;border:1px solid rgba(255,255,255,.18);border-radius:14px;background:rgba(255,255,255,.08);color:#fff;font-weight:700;cursor:pointer}.menu-status{font-size:14px;color:rgba(255,255,255,.62);padding:4px 0 8px}
      .nav-strip{height:78px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));align-items:stretch;border-top:1px solid var(--divider-color);background:var(--card-background-color);flex:0 0 auto;padding:4px 8px 6px;gap:4px}
      .nav-button{min-width:0;width:100%;height:64px;border:0;border-radius:14px;background:transparent;color:var(--secondary-text-color);cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;overflow:hidden;padding:0}
      .nav-button ha-icon{--mdc-icon-size:26px}.nav-button span{font-size:11px;font-weight:700;white-space:nowrap}.nav-button.active{color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 10%,transparent)}
      .browser-shell{min-height:640px;display:flex;flex-direction:column}.browser-header{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:10px;padding:14px;border-bottom:1px solid var(--divider-color)}.header-actions{display:flex;gap:8px}
      .browser-title{font-size:23px;font-weight:800;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.provider-row{display:flex;gap:8px;flex-wrap:wrap;padding:14px 14px 0}
      .provider-chip{border:1px solid var(--divider-color);border-radius:999px;padding:9px 13px;background:var(--secondary-background-color);color:var(--secondary-text-color);font-weight:700;cursor:pointer}
      .provider-chip.active{background:var(--primary-color);border-color:var(--primary-color);color:#fff}.search-row{display:grid;grid-template-columns:minmax(0,1fr) 48px auto;gap:9px;padding:12px 14px;align-items:center}
      .search-input{width:100%;min-width:0;padding:13px 15px;border-radius:14px;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);font-size:16px}
      .voice-search{width:48px;height:48px;border:1px solid var(--divider-color);border-radius:50%;background:var(--secondary-background-color);color:var(--primary-text-color);display:flex;align-items:center;justify-content:center;cursor:pointer}.voice-search ha-icon{--mdc-icon-size:25px}.voice-search.listening{background:var(--primary-color);color:#fff;border-color:var(--primary-color);box-shadow:0 0 0 5px color-mix(in srgb,var(--primary-color) 18%,transparent)}.voice-search:disabled{opacity:.35;cursor:default}.search-go,.primary-action,.secondary-action{border:0;border-radius:13px;padding:0 16px;font-weight:800;cursor:pointer}.search-go,.primary-action{background:var(--primary-color);color:#fff}.secondary-action{background:var(--secondary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color)}
      .browser-body{padding:2px 14px 20px;overflow:auto;flex:1}.status{padding:50px 10px;text-align:center;color:var(--secondary-text-color)}.status.error{color:var(--error-color)}
      .section{margin-bottom:24px}.section h3{font-size:20px;margin:10px 0 12px}.tile-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:14px}
      .media-tile{border:0;background:transparent;color:var(--primary-text-color);padding:0;text-align:left;cursor:pointer;min-width:0}.artwork{position:relative;aspect-ratio:1;border-radius:16px;overflow:hidden;background:var(--secondary-background-color);display:flex;align-items:center;justify-content:center}
      .artwork.round{border-radius:50%}.artwork img,.album-art img,.track-thumb img{width:100%;height:100%;object-fit:cover}.artwork ha-icon{--mdc-icon-size:48px;opacity:.45}
      .source-tag{position:absolute;right:7px;bottom:7px;padding:4px 7px;border-radius:999px;background:rgba(0,0,0,.7);color:#fff;font-size:10px}.tile-title{font-weight:750;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .tile-subtitle{font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.track-list{display:flex;flex-direction:column;gap:5px}
      .track-row{width:100%;display:grid;grid-template-columns:24px 50px minmax(0,1fr) auto;gap:10px;align-items:center;border:0;border-radius:12px;background:transparent;color:var(--primary-text-color);padding:6px;text-align:left;cursor:pointer}
      .track-row:hover{background:var(--secondary-background-color)}.track-index{color:var(--secondary-text-color);text-align:center}.track-thumb{width:50px;height:50px;border-radius:10px;overflow:hidden;background:var(--secondary-background-color);display:flex;align-items:center;justify-content:center}
      .track-copy{min-width:0}.track-row-title{font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.track-row-subtitle{font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .track-source{font-size:10px;color:var(--secondary-text-color);border:1px solid var(--divider-color);border-radius:999px;padding:4px 7px}
      .recent-list{display:flex;flex-direction:column;gap:6px}.recent-row{width:100%;display:grid;grid-template-columns:58px minmax(0,1fr) auto;gap:11px;align-items:center;border:0;border-radius:14px;background:transparent;color:var(--primary-text-color);padding:7px;text-align:left;cursor:pointer}.recent-row:hover{background:var(--secondary-background-color)}.recent-thumb{width:58px;height:58px;border-radius:12px;overflow:hidden;background:var(--secondary-background-color);display:flex;align-items:center;justify-content:center}.recent-thumb img{width:100%;height:100%;object-fit:cover}.recent-copy{min-width:0}.recent-title{font-weight:780;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.recent-subtitle{margin-top:3px;font-size:12px;color:var(--secondary-text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.recent-type{font-size:10px;color:var(--secondary-text-color);border:1px solid var(--divider-color);border-radius:999px;padding:4px 7px}
      .album-hero{display:grid;grid-template-columns:150px minmax(0,1fr);gap:16px;align-items:center;margin:10px 0 20px}
      .album-art{width:150px;height:150px;border-radius:18px;overflow:hidden;background:var(--secondary-background-color);display:flex;align-items:center;justify-content:center}.album-art ha-icon{--mdc-icon-size:56px}
      .album-name{font-size:24px;font-weight:850}.album-artist{margin-top:5px;color:var(--secondary-text-color)}.album-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.album-actions button{height:42px;display:flex;align-items:center;gap:7px}
      @media(max-width:600px){.search-row{grid-template-columns:minmax(0,1fr) 44px auto;gap:7px}.voice-search{width:44px;height:44px}.search-go{padding:0 13px}.nav-strip{padding-left:5px;padding-right:5px}.nav-button span{font-size:10px}.now-shell,.browser-shell{min-height:560px}.hero,.now-content{min-height:650px}.now-content{padding:18px 18px 16px}.artwork-stage{width:min(84vw,390px);margin-bottom:18px}.track-title{font-size:25px}.track-meta{font-size:15px}.transport{margin-left:16px;margin-right:16px}.bottom-actions{grid-template-columns:54px minmax(0,1fr) 54px}.bottom-circle{width:54px;height:54px}.destination-pill{height:54px;font-size:16px}.tile-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.album-hero{grid-template-columns:110px minmax(0,1fr)}.album-art{width:110px;height:110px}.track-source,.recent-type{display:none}.track-row{grid-template-columns:22px 46px minmax(0,1fr)}.recent-row{grid-template-columns:52px minmax(0,1fr)}.recent-thumb{width:52px;height:52px}.member-balance-row{grid-template-columns:96px minmax(0,1fr);gap:10px;min-height:64px}.member-trim{height:52px}.member-trim::-webkit-slider-thumb{width:34px;height:34px;margin-top:-13px}.member-trim::-moz-range-thumb{width:34px;height:34px}.balance-scale{padding-left:106px}.nav-button{min-width:0}}
    `;
  }

  getCardSize() {
    return 8;
  }
}

if (!customElements.get("family-music-card")) {
  customElements.define("family-music-card", FamilyMusicCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "family-music-card")) {
  window.customCards.push({
    type: "family-music-card",
    name: "Family Music",
    description: "Unified Music Assistant + Sonos family music controller",
    preview: true,
  });
}

console.info(`%c FAMILY-MUSIC-CARD %c v${CARD_VERSION} `, "background:#03a9f4;color:white;font-weight:bold", "background:#eee;color:#333");
