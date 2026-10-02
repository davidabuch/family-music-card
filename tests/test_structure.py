import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_card_exist():
    manifest_path = ROOT / "custom_components" / "family_music" / "manifest.json"
    card_path = ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == "family_music"
    assert manifest["version"] == "0.3.4"
    assert card_path.exists()


def test_hacs_metadata():
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"] == "Family Music Card"


def test_recents_and_favorites_commands_registered():
    init_path = ROOT / "custom_components" / "family_music" / "__init__.py"
    card_path = ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    init_text = init_path.read_text()
    card_text = card_path.read_text()
    assert '"family_music/recents"' in init_text
    assert '"family_music/favorites"' in init_text
    assert 'id="navRecents"' in card_text
    assert 'id="navFavorites"' in card_text


def test_recents_are_not_user_initiated_only_and_views_refresh():
    init_text = (ROOT / "custom_components" / "family_music" / "__init__.py").read_text()
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    recents_block = init_text.split('"family_music/recents"', 1)[1].split(
        '"family_music/favorites"', 1
    )[0]
    assert "user_initiated_only=True" not in recents_block
    assert "fully_played_only=False" in recents_block
    assert 'this._view === "favorites"' in card_text
    assert "setInterval" in card_text
    assert "1000" in card_text
    assert 'id="refreshView"' in card_text


def test_recents_fall_back_to_live_queue_history():
    init_text = (ROOT / "custom_components" / "family_music" / "__init__.py").read_text()
    recents_block = init_text.split('"family_music/recents"', 1)[1].split(
        '"family_music/favorites"', 1
    )[0]
    assert '"player_queues/get"' in recents_block
    assert '"player_queues/items"' in recents_block
    assert "current_index" in recents_block
    assert "reversed(played_queue_items)" in recents_block


def test_favorites_include_native_sonos_browser():
    init_text = (ROOT / "custom_components" / "family_music" / "__init__.py").read_text()
    assert '"favorites_folder"' in init_text
    assert '"object.container.album.musicAlbum"' in init_text
    assert '"object.container.playlistContainer"' in init_text
    assert '"object.item.audioItem.audioBroadcast"' in init_text
    assert "sonos_favorite" in init_text


def test_native_sonos_browse_supports_browsemedia_objects():
    init_text = (ROOT / "custom_components" / "family_music" / "__init__.py").read_text()
    assert "def _browse_value" in init_text
    assert 'getattr(node, key, default)' in init_text
    assert '_browse_value(child, "title")' in init_text
    assert '_browse_value(child, "thumbnail")' in init_text


def test_fast_refresh_and_volume_step_controls():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert "}, 1000);" in card_text
    assert "this._refreshTick % 3 === 0" in card_text
    assert 'id="volumeDown"' in card_text
    assert 'id="volumeUp"' in card_text
    assert "current - 1" in card_text
    assert "current + 1" in card_text
    assert 'addEventListener("input"' in card_text


def test_track_progress_and_mute_controls():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'id="trackProgress"' in card_text
    assert 'id="elapsed"' in card_text
    assert 'id="remaining"' in card_text
    assert "media_position_updated_at" in card_text
    assert "media_duration" in card_text
    assert 'id="muteToggle"' in card_text
    assert '"volume_mute"' in card_text
    assert "is_volume_muted" in card_text
    assert '"mdi:volume-off"' in card_text


def test_refined_progress_and_compact_volume_controls():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'id="trackProgress" class="track-progress" type="range"' in card_text
    assert "--progress-pct" in card_text
    assert 'class="volume-nudge"' in card_text
    assert "grid-template-columns:38px 32px minmax(0,1fr) 34px 32px" in card_text


def test_sonos_inspired_now_playing_and_seekable_progress():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert '"media_seek"' in card_text
    assert "seek_position" in card_text
    assert "this._seeking = true" in card_text
    assert "progress && !this._seeking" in card_text
    assert 'id="artImage"' in card_text
    assert 'id="openSearch"' in card_text
    assert 'id="destinationButton"' in card_text
    assert 'id="moreButton"' in card_text
    assert 'id="volumeValue"' in card_text
    assert "hero-bg" in card_text
    assert "destination-pill" in card_text



def test_group_member_volume_controls_live_in_more_menu():
    init_text = (ROOT / "custom_components" / "family_music" / "__init__.py").read_text()
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert '"family_music/group_members"' in init_text
    assert '"players/all"' in init_text
    assert 'entry.platform == "music_assistant"' in init_text
    assert '"group_members"' in init_text
    assert 'id="moreButton"' in card_text
    assert "Speaker volumes" in card_text
    assert 'class="member-volume"' in card_text
    assert 'player_entity_id: player' in card_text
    assert 'entity_id: entityId' in card_text
    assert '"volume_set"' in card_text



def test_optimistic_responsive_media_controls():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert "this._optimisticPlayback" in card_text
    assert "this._optimisticPlaybackUntil" in card_text
    assert "this._optimisticVolumes = new Map()" in card_text
    assert "this._volumeWrites = new Map()" in card_text
    assert "_queueVolumeWrite(entityId, percent, flush = false)" in card_text
    assert "elapsed >= 60" in card_text
    assert 'addEventListener("change"' in card_text
    assert 'targetState === "playing" ? "media_play" : "media_pause"' in card_text
    assert "Date.now() + 5000" in card_text



def test_now_playing_bottom_action_model():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'id="openSearch"' in card_text
    assert 'icon="mdi:magnify"' in card_text
    assert 'id="destinationButton"' in card_text
    assert "Change playback destination" in card_text
    assert 'id="moreButton"' in card_text
    assert 'icon="mdi:dots-horizontal"' in card_text
    assert "_renderDestinationMenu()" in card_text
    assert "_renderMoreMenu()" in card_text
    assert "Play in" in card_text


def test_main_volume_keeps_mute_and_one_point_nudges():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'id="muteToggle"' in card_text
    assert 'id="volumeDown"' in card_text
    assert 'id="volumeUp"' in card_text
    assert 'id="volumeValue"' in card_text
    assert "current - 1" in card_text
    assert "current + 1" in card_text



def test_group_member_volume_rows_have_one_point_nudges():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'class="member-volume-nudge member-volume-down"' in card_text
    assert 'class="member-volume-nudge member-volume-up"' in card_text
    assert 'data-delta="-1"' in card_text
    assert 'data-delta="1"' in card_text
    assert "current + delta" in card_text
    assert "_queueVolumeWrite(entityId, next, true)" in card_text


def test_volume_drag_coalescing_is_faster():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert "elapsed >= 60" in card_text
    assert "Math.max(0, 60 - elapsed)" in card_text



def test_optimistic_volume_waits_for_exact_acknowledgement():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert "actualPercent === pending.value" in card_text
    assert "Math.abs(actualPercent - pending.value) <= 1" not in card_text
    assert "Date.now() + 5000" in card_text



def test_browser_navigation_is_even_four_column_grid():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in card_text
    assert ".nav-button{min-width:0;width:100%" in card_text


def test_voice_search_controls_and_feature_detection():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'id="voiceSearch"' in card_text
    assert 'icon="mdi:microphone"' in card_text
    assert "window.SpeechRecognition || window.webkitSpeechRecognition" in card_text
    assert 'recognition.lang = "en-US"' in card_text
    assert "recognition.onresult" in card_text
    assert "setTimeout(() => this._search(), 0)" in card_text
    assert "Dictation is not available on this device" in card_text



def test_now_playing_uses_music_search_icon():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert 'class="bottom-circle music-search-button"' in card_text
    assert 'class="music-search-icon"' in card_text
    assert 'id="musicSearchGradient"' in card_text
    assert 'class="music-search-note"' in card_text
    assert 'class="music-search-capsule"' in card_text
    assert 'icon="mdi:magnify"' not in card_text.split('id="openSearch"', 1)[1].split('</button>', 1)[0]
