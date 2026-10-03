from config import RARITY_ORDER
from utils.rarity import get_rarity_button_emoji, get_rarity_custom_emoji_id, get_rarity_emoji


def test_all_rarities_have_resolvable_emoji_helpers():
    assert len(RARITY_ORDER) == 10
    for rarity in RARITY_ORDER:
        assert isinstance(get_rarity_emoji(rarity), str)
        assert isinstance(get_rarity_button_emoji(rarity), str)
        assert isinstance(get_rarity_custom_emoji_id(rarity), str)


def test_custom_emoji_markup_is_well_formed_when_configured(monkeypatch):
    import utils.rarity as rarity_module

    target = RARITY_ORDER[-1]
    monkeypatch.setitem(rarity_module._RARITY_CUSTOM_EMOJI_IDS, target, "1234567890123456789")
    monkeypatch.setitem(rarity_module._RARITY_FALLBACK_EMOJIS, target, "🔵")

    value = get_rarity_emoji(target)
    assert value == '<tg-emoji emoji-id="1234567890123456789">🔵</tg-emoji>'
    assert get_rarity_custom_emoji_id(target) == "1234567890123456789"
    assert get_rarity_button_emoji(target) == "🔵"
