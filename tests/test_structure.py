import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_card_exist():
    manifest_path = ROOT / "custom_components" / "family_music" / "manifest.json"
    card_path = ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == "family_music"
    assert manifest["version"] == "0.2.1"
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
    assert "current - 5" in card_text
    assert "current + 5" in card_text
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
    assert "grid-template-columns:34px 24px minmax(0,1fr) 24px" in card_text


def test_glass_player_and_seekable_progress():
    card_text = (
        ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    ).read_text()
    assert '"media_seek"' in card_text
    assert "seek_position" in card_text
    assert "this._seeking = true" in card_text
    assert "progress && !this._seeking" in card_text
    assert "backdrop-filter:blur(22px) saturate(165%)" in card_text
    assert "background:rgba(255,255,255,.18)" in card_text
    assert "pointer-events:none" not in card_text
