const CARD_VERSION = "0.2.2";

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
    try {
      const members = await this._ws("family_music/group_members", {
        player_entity_id: player,
      });
      if (this._selectedPlayer === player) {
        this._groupMembers = Array.isArray(members) ? members : [];
        this._groupMembersFor = player;
      }
    } catch (_error) {
      if (this._selectedPlayer === player) {
        this._groupMembers = [];
        this._groupMembersFor = player;
      }
    } finally {
      this._groupMembersLoading = false;
      if (this._view === "now" && this._selectedPlayer === player) {
        this._renderMemberVolumePanel();
      }
    }
  }

  _memberVolumeMarkup() {
    if (!this._isSelectedGroup()) return "";
    const count = this._groupMembers.length;
    const label = count ? `Speakers · ${count}` : "Speakers";
    const chevron = this._membersExpanded ? "mdi:chevron-up" : "mdi:chevron-down";
    const rows = this._membersExpanded
      ? this._groupMembers.map((member) => {
          const state = this._hass?.states?.[member.entity_id];
          const volume = state?.attributes?.volume_level;
          const value = volume == null ? 0 : Math.round(volume * 100);
          const disabled = !member.available || !state ? "disabled" : "";
          return `
            <div class="member-volume-row" data-member="${this._escape(member.entity_id)}">
              <div class="member-volume-name">${this._escape(member.name)}</div>
              <input class="member-volume" data-entity="${this._escape(member.entity_id)}"
                type="range" min="0" max="100" step="1" value="${value}" ${disabled}
                aria-label="${this._escape(member.name)} volume">
              <div class="member-volume-value">${value}%</div>
            </div>
          `;
        }).join("")
      : "";
    const empty = this._membersExpanded && !this._groupMembersLoading && !count
      ? '<div class="member-volume-empty">No group members found.</div>'
      : "";
    const loading = this._membersExpanded && this._groupMembersLoading
      ? '<div class="member-volume-empty">Loading speakers…</div>'
      : "";
    return `
      <div class="member-volume-panel">
        <button id="toggleMembers" class="member-volume-toggle" type="button">
          <span>${label}</span><ha-icon icon="${chevron}"></ha-icon>
        </button>
        <div id="memberVolumeRows">${loading}${empty}${rows}</div>
      </div>
    `;
  }

  _renderMemberVolumePanel() {
    const host = this.shadowRoot.getElementById("memberVolumeHost");
    if (!host) return;
    host.innerHTML = this._memberVolumeMarkup();
    this._wireMemberVolumePanel();
  }

  _wireMemberVolumePanel() {
    this.shadowRoot.getElementById("toggleMembers")?.addEventListener("click", async () => {
      this._membersExpanded = !this._membersExpanded;
      this._renderMemberVolumePanel();
      if (this._membersExpanded) await this._loadGroupMembers(true);
    });
    this.shadowRoot.querySelectorAll(".member-volume").forEach((slider) => {
      slider.addEventListener("input", (event) => {
        const value = Math.max(0, Math.min(100, Number(event.target.value || 0)));
        const row = event.target.closest(".member-volume-row");
        const display = row?.querySelector(".member-volume-value");
        if (display) display.textContent = `${Math.round(value)}%`;
      });
      slider.addEventListener("change", async (event) => {
        const entityId = event.target.dataset.entity;
        if (!entityId) return;
        const value = Math.max(0, Math.min(100, Number(event.target.value || 0)));
        await this._hass.callService("media_player", "volume_set", {
          entity_id: entityId,
          volume_level: value / 100,
        });
      });
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
    return `
      <div class="now-shell">
        <div class="topbar">
          <select id="playerSelect" class="player-select"></select>
          <button id="openSearch" class="circle-button search-button" title="Choose music" aria-label="Choose music">
            <ha-icon icon="mdi:magnify"></ha-icon>
          </button>
        </div>
        <div id="hero" class="hero">
          <div class="hero-placeholder"><ha-icon icon="mdi:music"></ha-icon></div>
          <div class="hero-shade"></div>
          <div class="track-card">
            <div id="trackTitle" class="track-title">Nothing playing</div>
            <div id="trackMeta" class="track-meta">—</div>
          </div>
          <div class="controls-card">
            <div class="transport">
              <button id="prev" class="transport-button"><ha-icon icon="mdi:skip-previous"></ha-icon></button>
              <button id="playPause" class="play-button"><ha-icon icon="mdi:play"></ha-icon></button>
              <button id="next" class="transport-button"><ha-icon icon="mdi:skip-next"></ha-icon></button>
            </div>
            <div class="progress-wrap">
              <div class="progress-time"><span id="elapsed">0:00</span><span id="remaining">-0:00</span></div>
              <input id="trackProgress" class="track-progress" type="range" min="0" max="100" step="0.1" value="0" tabindex="-1" aria-label="Track progress">
            </div>
            <div class="volume-row">
              <button id="muteToggle" class="mute-toggle" title="Mute" aria-label="Mute">
                <ha-icon icon="mdi:volume-high"></ha-icon>
              </button>
              <button id="volumeDown" class="volume-nudge" title="Volume down" aria-label="Volume down">
                <ha-icon icon="mdi:chevron-left"></ha-icon>
              </button>
              <input id="volume" class="volume" type="range" min="0" max="100" step="1" value="20">
              <button id="volumeUp" class="volume-nudge" title="Volume up" aria-label="Volume up">
                <ha-icon icon="mdi:chevron-right"></ha-icon>
              </button>
            </div>
            <div id="memberVolumeHost">${this._memberVolumeMarkup()}</div>
          </div>
        </div>
        ${this._renderNavStrip("now")}
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

  _setVolume(percent) {
    if (!this._selectedPlayer) return;
    const bounded = Math.max(0, Math.min(100, Math.round(percent)));
    const slider = this.shadowRoot.getElementById("volume");
    if (slider) slider.value = bounded;
    this._hass.callService("media_player", "volume_set", {
      entity_id: this._selectedPlayer,
      volume_level: bounded / 100,
    });
  }

  _wire() {
    const playerSelect = this.shadowRoot.getElementById("playerSelect");
    playerSelect?.addEventListener("change", (event) => {
      this._selectedPlayer = event.target.value;
      this._groupMembers = [];
      this._groupMembersFor = null;
      this._membersExpanded = false;
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
    this.shadowRoot.getElementById("navNow")?.addEventListener("click", () => this._setView("now"));
    this.shadowRoot.getElementById("navRecents")?.addEventListener("click", () => this._openRecents());
    this.shadowRoot.getElementById("navFavorites")?.addEventListener("click", () => this._openFavorites());

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
      const state = this._hass?.states?.[this._selectedPlayer]?.state;
      if (this._selectedPlayer) {
        this._hass.callService("media_player", state === "playing" ? "media_pause" : "media_play", {
          entity_id: this._selectedPlayer,
        });
      }
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
      const muted = Boolean(
        this._hass?.states?.[this._selectedPlayer]?.attributes?.is_volume_muted
      );
      this._hass.callService("media_player", "volume_mute", {
        entity_id: this._selectedPlayer,
        is_volume_muted: !muted,
      });
    });
    this.shadowRoot.getElementById("volume")?.addEventListener("input", (event) => {
      this._setVolume(Number(event.target.value));
    });
    this.shadowRoot.getElementById("volumeDown")?.addEventListener("click", () => {
      const current = Number(this.shadowRoot.getElementById("volume")?.value || 0);
      this._setVolume(current - 5);
    });
    this.shadowRoot.getElementById("volumeUp")?.addEventListener("click", () => {
      const current = Number(this.shadowRoot.getElementById("volume")?.value || 0);
      this._setVolume(current + 5);
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

    this.shadowRoot.getElementById("playAlbum")?.addEventListener("click", () => this._playAlbum(false));
    this.shadowRoot.getElementById("shuffleAlbum")?.addEventListener("click", () => this._playAlbum(true));

    this._wireMemberVolumePanel();
    this._wireBrowserItems();
    this._loadGroupMembers(false);
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
    const hero = this.shadowRoot.getElementById("hero");

    if (title) title.textContent = attrs.media_title || "Nothing playing";
    if (meta) {
      meta.textContent =
        [attrs.media_artist, attrs.media_album_name].filter(Boolean).join(" · ") || "—";
    }
    if (playPause) {
      playPause.innerHTML = `<ha-icon icon="${state.state === "playing" ? "mdi:pause" : "mdi:play"}"></ha-icon>`;
    }
    if (volume && attrs.volume_level != null) {
      volume.value = Math.round(attrs.volume_level * 100);
    }
    if (this._membersExpanded && this._groupMembersFor === this._selectedPlayer) {
      this._groupMembers.forEach((member) => {
        const memberState = this._hass.states[member.entity_id];
        const memberVolume = memberState?.attributes?.volume_level;
        if (memberVolume == null) return;
        const slider = this.shadowRoot.querySelector(
          `.member-volume[data-entity="${CSS.escape(member.entity_id)}"]`
        );
        if (slider && document.activeElement !== slider) {
          slider.value = Math.round(memberVolume * 100);
          const row = slider.closest(".member-volume-row");
          const display = row?.querySelector(".member-volume-value");
          if (display) display.textContent = `${Math.round(memberVolume * 100)}%`;
        }
      });
    }
    if (muteToggle) {
      const muted = Boolean(attrs.is_volume_muted);
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
    if (hero) {
      let image = hero.querySelector(":scope > img.hero-image");
      if (picture) {
        if (!image) {
          image = document.createElement("img");
          image.className = "hero-image";
          hero.prepend(image);
        }
        if (image.getAttribute("src") !== picture) image.setAttribute("src", picture);
      } else if (image) {
        image.remove();
      }
    }
  }

  _styles() {
    return `
      :host{display:block}*{box-sizing:border-box}button,input,select{font:inherit}
      .card{overflow:hidden;border-radius:22px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
      .now-shell{min-height:640px;display:flex;flex-direction:column}.topbar{display:grid;grid-template-columns:minmax(0,1fr) 48px;gap:10px;align-items:center;padding:14px}
      .player-select{width:100%;padding:12px 14px;border-radius:14px;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);font-weight:700}
      .circle-button{width:44px;height:44px;border-radius:50%;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);display:flex;align-items:center;justify-content:center;cursor:pointer}
      .search-button{width:48px;height:48px}.hero{position:relative;flex:1;min-height:530px;background:var(--secondary-background-color);overflow:hidden;display:flex;align-items:center;justify-content:center}
      .hero-image{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.hero-placeholder ha-icon{--mdc-icon-size:84px;opacity:.28}.hero-shade{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(0,0,0,.08),rgba(0,0,0,.18) 45%,rgba(0,0,0,.58))}
      .track-card{position:absolute;left:16px;right:16px;top:16px;padding:12px 14px;border-radius:18px;background:rgba(255,255,255,.20);color:#fff;backdrop-filter:blur(22px) saturate(165%);-webkit-backdrop-filter:blur(22px) saturate(165%);border:1px solid rgba(255,255,255,.28);box-shadow:0 8px 26px rgba(0,0,0,.12);z-index:2;text-shadow:0 1px 3px rgba(0,0,0,.28)}
      .track-title{font-size:23px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.track-meta{margin-top:3px;font-size:15px;color:rgba(255,255,255,.82);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .controls-card{position:absolute;left:16px;right:16px;bottom:16px;padding:10px 14px 11px;border-radius:18px;background:rgba(255,255,255,.18);color:#fff;backdrop-filter:blur(22px) saturate(165%);-webkit-backdrop-filter:blur(22px) saturate(165%);border:1px solid rgba(255,255,255,.26);box-shadow:0 8px 26px rgba(0,0,0,.12);z-index:2}
      .transport{display:flex;align-items:center;justify-content:center;gap:16px}.transport-button,.play-button{border:0;border-radius:50%;display:flex;align-items:center;justify-content:center;cursor:pointer}
      .transport-button{width:40px;height:40px;background:transparent;color:rgba(255,255,255,.92)}.play-button{width:56px;height:56px;background:rgba(55,55,55,.62);color:#fff;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}.play-button ha-icon{--mdc-icon-size:30px}.transport-button ha-icon{--mdc-icon-size:27px}
      .progress-wrap{margin-top:6px}.progress-time{display:flex;justify-content:space-between;font-size:11px;color:rgba(255,255,255,.82);margin-bottom:2px}.track-progress{width:100%;height:18px;margin:0;appearance:none;-webkit-appearance:none;background:transparent;cursor:pointer}.track-progress::-webkit-slider-runnable-track{height:4px;border-radius:999px;background:linear-gradient(to right,var(--primary-color) 0 var(--progress-pct,0%),rgba(255,255,255,.34) var(--progress-pct,0%) 100%)}.track-progress::-webkit-slider-thumb{-webkit-appearance:none;width:14px;height:14px;border-radius:50%;background:var(--primary-color);margin-top:-5px;box-shadow:0 0 0 2px rgba(255,255,255,.88);cursor:pointer}.track-progress::-moz-range-track{height:4px;border-radius:999px;background:rgba(0,0,0,.14)}.track-progress::-moz-range-progress{height:4px;border-radius:999px;background:var(--primary-color)}.track-progress::-moz-range-thumb{width:12px;height:12px;border:0;border-radius:50%;background:var(--primary-color)}
      .volume-row{display:grid;grid-template-columns:34px 22px minmax(0,1fr) 22px;gap:4px;align-items:center;margin-top:4px}.volume{width:100%;height:18px;margin:0}.mute-toggle{width:34px;height:34px;border:0;background:transparent;color:rgba(255,255,255,.94);display:flex;align-items:center;justify-content:center;cursor:pointer;border-radius:50%}.mute-toggle ha-icon{--mdc-icon-size:25px}.mute-toggle.muted{background:rgba(55,55,55,.62);color:#fff}.volume-nudge{width:22px;height:26px;border:0;background:transparent;color:rgba(255,255,255,.88);display:flex;align-items:center;justify-content:center;cursor:pointer;padding:0}.volume-nudge ha-icon{--mdc-icon-size:17px}
      .member-volume-panel{margin-top:2px;border-top:1px solid rgba(255,255,255,.18);padding-top:2px}.member-volume-toggle{width:100%;height:28px;padding:0 2px;border:0;background:transparent;color:rgba(255,255,255,.88);display:flex;align-items:center;justify-content:space-between;font-size:11px;font-weight:700;cursor:pointer}.member-volume-toggle ha-icon{--mdc-icon-size:18px}.member-volume-row{display:grid;grid-template-columns:minmax(72px,1fr) minmax(110px,2fr) 34px;gap:8px;align-items:center;min-height:30px}.member-volume-name{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:rgba(255,255,255,.9)}.member-volume{width:100%;height:18px;margin:0}.member-volume-value{font-size:10px;text-align:right;color:rgba(255,255,255,.76)}.member-volume-empty{font-size:11px;color:rgba(255,255,255,.72);padding:5px 2px 3px}
      .nav-strip{height:72px;display:flex;align-items:center;justify-content:space-around;border-top:1px solid var(--divider-color);background:var(--card-background-color);flex:0 0 auto}
      .nav-button{min-width:64px;height:58px;border:0;background:transparent;color:var(--secondary-text-color);cursor:pointer;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px}.nav-button ha-icon{--mdc-icon-size:27px}.nav-button span{font-size:11px;font-weight:700}.nav-button.active{color:var(--primary-color)}
      .browser-shell{min-height:640px;display:flex;flex-direction:column}.browser-header{display:grid;grid-template-columns:44px minmax(0,1fr) auto;align-items:center;gap:10px;padding:14px;border-bottom:1px solid var(--divider-color)}.header-actions{display:flex;gap:8px}
      .browser-title{font-size:23px;font-weight:800;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.provider-row{display:flex;gap:8px;flex-wrap:wrap;padding:14px 14px 0}
      .provider-chip{border:1px solid var(--divider-color);border-radius:999px;padding:9px 13px;background:var(--secondary-background-color);color:var(--secondary-text-color);font-weight:700;cursor:pointer}
      .provider-chip.active{background:var(--primary-color);border-color:var(--primary-color);color:#fff}.search-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;padding:12px 14px}
      .search-input{width:100%;padding:13px 15px;border-radius:14px;border:1px solid var(--divider-color);background:var(--secondary-background-color);color:var(--primary-text-color);font-size:16px}
      .search-go,.primary-action,.secondary-action{border:0;border-radius:13px;padding:0 16px;font-weight:800;cursor:pointer}.search-go,.primary-action{background:var(--primary-color);color:#fff}.secondary-action{background:var(--secondary-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color)}
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
      @media(max-width:600px){.now-shell,.browser-shell{min-height:560px}.hero{min-height:450px}.tile-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.album-hero{grid-template-columns:110px minmax(0,1fr)}.album-art{width:110px;height:110px}.track-source,.recent-type{display:none}.track-row{grid-template-columns:22px 46px minmax(0,1fr)}.recent-row{grid-template-columns:52px minmax(0,1fr)}.recent-thumb{width:52px;height:52px}.nav-button{min-width:58px}}
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
