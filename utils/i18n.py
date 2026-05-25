from __future__ import annotations

from locales.lang import LANG

DEFAULT_LANG = "en"


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """Return translated text by key and format it safely.

    Usage:
        t("wrong_name", guess="yelan", arrow="⬆️")

    If a key is missing, the key itself is returned to make missing text easy to spot.
    """
    text = LANG.get(lang, LANG[DEFAULT_LANG]).get(key, key)
    if kwargs:
        return text.format(**kwargs)
    return text
