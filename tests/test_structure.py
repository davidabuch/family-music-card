import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_card_exist():
    manifest_path = ROOT / "custom_components" / "family_music" / "manifest.json"
    card_path = ROOT / "custom_components" / "family_music" / "www" / "family-music-card.js"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["domain"] == "family_music"
    assert manifest["version"] == "0.1.2"
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
