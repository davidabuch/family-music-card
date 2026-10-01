import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_card_exist():
    manifest_path = ROOT / "custom_components" / "family_music" / "manifest.json"
    card_path = ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == "family_music"
    assert manifest["version"] == "0.1.7"
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
    assert "10000" in card_text
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
