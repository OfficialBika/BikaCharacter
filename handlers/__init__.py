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
from handlers.profile import register_profile_handlers
from handlers.rankings import register_ranking_handlers
from handlers.start import register_start_handlers


def register_handlers(app: Application) -> None:
    # Bot group join/leave events.
    register_group_event_handlers(app)

    # Specific command handlers first.
    register_start_handlers(app)
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
