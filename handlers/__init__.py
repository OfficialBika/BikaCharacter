from telegram.ext import Application

from handlers.admin import register_admin_handlers
from handlers.check import register_check_handlers
from handlers.claim import register_claim_handlers
from handlers.drop import register_drop_handlers
from handlers.fav import register_fav_handlers
from handlers.gift import register_gift_handlers
from handlers.group_events import register_group_event_handlers
from handlers.inline import register_inline_handlers
from handlers.harem import register_harem_handlers
from handlers.hmode import register_hmode_handlers
from handlers.photo_add import register_photo_add_handlers

# Public /profile uses the new generated-image implementation.
from handlers.profile import register_profile_handlers

# Backward compatibility for owner_tools.py, which still imports the old profile
# helper functions. Install these attributes before importing owner_tools.
import handlers.profile as _profile_module
from handlers.profile_legacy_compat import (
    build_profile_text as _legacy_build_profile_text,
    hydrate_card_media as _legacy_hydrate_card_media,
    reply_profile_media as _legacy_reply_profile_media,
)

_profile_module.build_profile_text = _legacy_build_profile_text
_profile_module.hydrate_card_media = _legacy_hydrate_card_media
_profile_module.reply_profile_media = _legacy_reply_profile_media

from handlers.rankings import register_ranking_handlers
from handlers.search import register_search_handlers
from handlers.start import register_start_handlers
from handlers.checkgp import register_checkgp_handlers
from handlers.ban_guard import register_ban_guard_handlers
from handlers.owner_tools import register_owner_tools_handlers


def register_handlers(app: Application) -> None:
    # Global ban guard runs before every normal bot handler.
    register_ban_guard_handlers(app)
    register_owner_tools_handlers(app)

    # Bot group join/leave events.
    register_group_event_handlers(app)
    register_checkgp_handlers(app)

    # Specific command handlers first.
    register_start_handlers(app)
    register_search_handlers(app)
    register_admin_handlers(app)
    register_photo_add_handlers(app)
    register_claim_handlers(app)
    register_check_handlers(app)
    register_profile_handlers(app)
    register_harem_handlers(app)
    register_hmode_handlers(app)
    register_fav_handlers(app)
    register_gift_handlers(app)
    register_ranking_handlers(app)
    register_inline_handlers(app)

    # Generic group message counter must be registered last.
    register_drop_handlers(app)
