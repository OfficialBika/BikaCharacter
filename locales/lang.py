from __future__ import annotations

# Central text templates for BIKA Character Bot.
# Keep HTML tags only in templates that are sent with parse_mode=HTML / reply_html.
# Dynamic user/card values should be escaped before formatting when they can contain user input.

LANG = {
    "en": {
        # Start
        "start_button_add_group": "➕ ADD ME TO YOUR GROUP",
        "start_button_support": "💬 Support Group",
        "start_button_update": "📢 Update Channel",
        "start_message": (
            "ʜᴇLLO {mention} !\n\n"
            "ɪ'ᴍ <b>Bika Character Bot</b> .\n\n"
            "ᴀ ᴄᴜᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜɪɴɢ ᴀᴅᴠᴇɴᴛᴜʀᴇ. "
            "ᴀᴅᴅ ᴍᴇ ᴛᴏ ᴀ ɢʀᴏᴜᴘ, ᴄᴏʟʟᴇᴄᴛ ꜰᴀꜱᴛ, "
            "ᴀɴᴅ ʙᴜɪʟᴅ ʏᴏᴜʀ ʜᴀʀᴇᴍ."
        ),

        # Common / callbacks
        "not_your_action": "Not your action.",
        "not_allowed": "Not allowed.",
        "invalid_mode": "Invalid mode.",
        "updated": "Updated.",
        "cancelled": "Cancelled.",
        "failed": "Failed.",
        "unknown": "Unknown",

        # Hmode
        "hmode_button_default": "🦖 DEFAULT",
        "hmode_button_detailed": "🦕 DETAILED",
        "hmode_button_reset": "🔄 RESET",
        "hmode_intro": "YOU CAN CHANGE YOUR HAREM INTERFACE USING THESE BUTTONS",
        "hmode_set": "✅ Harem view set to {mode}.",

        # Check / inline
        "check_usage": "Usage: /check <card id>",
        "check_not_found": "❌ Character ID {card_id} not found.",
        "card_check_header": "<b>OwO! Check out this character!</b>",
        "rarity_line": "({emoji} <b>RARITY:</b> {rarity})",
        "caught_globally": "🌍 <b>CAUGHT GLOBALLY:</b> {total} TIMES",
        "top10_catchers": "🏅 <b>TOP 10 CATCHERS OF THIS CHARACTER!</b>",
        "no_catch_data": "↪ No catch data yet",
        "inline_description": "{anime} | {rarity} | ID: {card_id}",

        # Harem
        "harem_header": "📘 {name}'s RECENT CHARACTERS - PAGE: {page}/{total_pages}",
        "harem_summary": "🎴 Total Cards: {total_cards} | 📚 Total Series: {total_series} | 🧩 Mode: {mode}",
        "harem_favourite": "💖 Favourite: {name} [{card_id}]",
        "harem_no_cards": "No cards yet.",
        "harem_no_cards_user": "You don't have any cards yet.",
        "harem_no_cards_alert": "No cards.",
        "harem_button_back": "🟦 ⬅ Back",
        "harem_button_next": "Next ➡ 🟩",

        # Profile
        "profile_header": "🎗BIKA CATCHER PROFILE🎗",
        "profile_user": "👤 USER: {username}",
        "profile_user_id": "🆔 USER ID: {user_id}",
        "profile_total_character": "⚡ TOTAL CHARACTER: {total_owned} ({unique_owned})",
        "profile_harem": "🫧 HAREM: {unique_owned}/{total_photo_count} ({percent:.3f}%)",
        "profile_level": "ℹ️ EXPERIENCE LEVEL: {level}",
        "profile_progress": "📈 PROGRESS BAR: {bar}",
        "profile_favourite": "💖 FAVOURITE: {name} [{card_id}]",
        "profile_favourite_not_set": "💖 FAVOURITE: Not set",
        "profile_rarity_line": "{emoji} RARITY {rarity}: {unique} ({total})",

        # Favourite
        "fav_not_set": "💖 Favourite is not set.\nUse: /fav <card id>",
        "fav_current_caption": "💖 Your favourite character\n{emoji} {name} [{card_id}]\nAnime: {anime}",
        "fav_missing_collection": "This character does not exist in your collection.",
        "fav_confirm": "DO YOU WANT TO SET THIS CHARACTER AS YOUR FAVOURITE?\n↪ {name} ({anime})",
        "fav_button_yes": "🟢 Yes",
        "fav_button_no": "🔴 No",
        "fav_card_missing": "Card missing.",
        "fav_set": "💖 Favourite set to {name} [{card_id}]",
        "fav_updated": "Favourite updated.",
        "fav_cancelled": "❌ Favourite update cancelled.",

        # Gift
        "gift_reply_target": "❌ Reply to the target user's message.\nExample: .gift 1001",
        "gift_usage": "Usage: .gift <card id> [qty]",
        "gift_self": "❌ You can't gift to yourself.",
        "gift_card_not_found_inventory": "❌ Card not found in your inventory.",
        "gift_not_enough": "❌ Not enough quantity.",
        "gift_preview": (
            "🎁 <b>GIFT PREVIEW</b>\n\n"
            "From: {sender}\n"
            "To: {receiver}\n"
            "Card: {emoji} {name}\n"
            "ID: {card_id}\n"
            "Anime: {anime}\n"
            "Qty: {qty}\n\n"
            "Are you sure you want to send this card?"
        ),
        "gift_button_confirm": "✅ Confirm",
        "gift_button_cancel": "❌ Cancel",
        "gift_not_your": "Not your gift action.",
        "gift_success": "✅ Gift sent successfully.\n\nCard: {emoji} {name}\nID: {card_id}\nQty: {qty}",
        "gift_confirmed": "Gift confirmed.",
        "gift_not_your_cancel": "Not your cancel action.",
        "gift_cancelled": "❌ Gift cancelled.",

        # Rankings
        "rank_no_group": "No group catch ranking yet.",
        "rank_group_header": "🏆 <b>TOP GROUP RANKING</b>",
        "rank_group_subtitle": "<b>/bika catches ranking</b>",
        "rank_group_row": "{rank} {group} — <b>{count}</b> catches",
        "rank_no_global": "No global harem ranking yet.",
        "rank_global_header": "🌍 <b>GLOBAL TOP 10 USERS</b>",
        "rank_global_subtitle": "<b>By total harem characters</b>",
        "rank_global_row": "{rank} {user} — <b>{total}</b> total | {unique} unique",
        "rank_no_today": "No catches yet today.\nDate: {date} ({timezone})",
        "rank_today_header": "📅 <b>TODAY GLOBAL TOP 10</b>",
        "rank_today_date": "Date: <b>{date}</b> ({timezone})",
        "rank_today_subtitle": "<b>By /bika catches today</b>",
        "rank_today_row": "{rank} {user} — <b>{count}</b> catches",
        "mylimit": "🎯 Daily Catch Limit\n\nDate: {date} ({timezone})\nUsed: {used}/{limit}\nRemaining: {remaining}",

        # Drop
        "bot_muted": "🤐 {name}, you sent too many messages in a row. Bot will ignore you for {minutes} minutes.",
        "pre_spawn_captcha": (
            "🧩 <b>HIGH RARITY CAPTCHA</b>\n\n"
            "{emoji} <b>{rarity}</b> card is trying to spawn in <b>{group_name}</b>.\n\n"
            "Solve this captcha within <b>{seconds}s</b>.\n"
            "✅ Correct answer = character will spawn.\n"
            "❌ Wrong answer or timeout = this drop will be lost.\n\n"
            "Question: <b>{question}</b>"
        ),
        "pre_spawn_timeout": "⌛ <b>CAPTCHA TIMEOUT</b>\n\n120 seconds finished. This scheduled high-rarity spawn has been lost.",
        "captcha_invalid": "Invalid captcha.",
        "captcha_finished": "Captcha already finished.",
        "pre_spawn_wrong": "❌ <b>WRONG CAPTCHA</b>\n\nThis scheduled high-rarity spawn has been lost.",
        "pre_spawn_wrong_alert": "Wrong. Spawn lost.",
        "pre_spawn_solved": "✅ <b>CAPTCHA SOLVED</b>\n\nHigh-rarity character is spawning now!",
        "solved": "Solved.",
        "spawn_no_card": "❌ No card found for this scheduled rarity. Spawn lost.",
        "spawn_caption": "{emoji} A new Character has spawned in {group_name} .\n\nTo own this character, send the character name quickly using /bika name .",

        # Claim
        "claim_captcha_timeout": "⌛ <b>CAPTCHA TIMEOUT</b>\n\n120 seconds finished. This high-rarity card has been lost.",
        "claim_success": (
            "🎉 <b>YOU GOT A NEW CHARACTER!</b>\n\n"
            "👤 Claimed by: {claimer}\n"
            "{emoji} Name: <b>{name}</b>\n"
            "🆔 ID: <b>{card_id}</b>\n"
            "🏷 RARITY: <b>{rarity}</b>\n"
            "🌴 ANIME: <b>{anime}</b>\n\n"
            "❄️ CHECK YOUR /harem !"
        ),
        "daily_limit": "❌ Daily catch limit reached.\nMyanmar/Yangon date: {date}\nUsed: {used}/{limit}\nRemaining: {remaining}",
        "claim_captcha_active": "⏳ Captcha is already active for this drop. Wait for the result.",
        "character_unavailable": "❌ This character is no longer available.",
        "claim_captcha_required": (
            "🧩 <b>CAPTCHA SOLVE REQUIRED</b>\n\n"
            "{emoji} <b>{rarity}</b> and above must pass captcha.\n"
            "👤 Player: {player}\n"
            "🎴 Card: <b>{card_name}</b> [{card_id}]\n\n"
            "Solve within <b>{seconds}s</b> or this card will be lost.\n"
            "Question: <b>{question}</b>"
        ),
        "no_character_available": "❌ No character is available right now.",
        "already_caught": "❌ <b>CHARACTER ALREADY CAUGHT</b>\n\nCaught by: {caught_by}\n\n🥤 Wait for new character to spawn.",
        "claim_high_captcha_active": "⏳ Captcha is already active for this high-rarity drop.",
        "wrong_name": "❌ CHARACTER NAME {guess} IS INCORRECT\n\n{arrow} CHARACTER is still available.",
        "wrong_name_empty": "❌ CHARACTER NAME IS INCORRECT\n\n{arrow} CHARACTER is still available.",
        "drop_data_missing": "❌ Drop data missing.",
        "captcha_not_for_you": "This captcha is not for you.",
        "captcha_expired": "Expired.",
        "claim_wrong_captcha": "❌ <b>WRONG CAPTCHA</b>\n\nThis high-rarity card has been lost.",
        "claim_wrong_card_lost": "Wrong. Card lost.",
        "daily_limit_card_lost": "❌ <b>DAILY LIMIT REACHED</b>\n\nUsed: {used}/{limit}. This card has been lost.",
        "daily_limit_reached_alert": "Daily limit reached.",
        "card_no_longer_available": "This card is no longer available.",
        "claim_captcha_solved": "✅ <b>CAPTCHA SOLVED</b>\n\n{user} got the card.",
        "captcha_solved_alert": "Captcha solved.",

        # Admin
        "group_admin_only": "❌ Group admin only.",
        "changetime_usage": "Usage: /changetime <number>\nGroup admin: {admin_min}-{admin_max}\nOwner: {owner_min}-{owner_max}",
        "changetime_range": "❌ changetime must be between {min_v} and {max_v}.",
        "changetime_updated": "✅ Changetime updated to {value} messages.",
        "admin_dashboard": (
            "⚙️ BIKA ADMIN DASHBOARD\n\n"
            "👤 Users: {users}\n"
            "👥 Groups: {groups}\n"
            "🖼 Cards: {cards}\n"
            "🎁 Transfers: {transfers}\n"
            "🤐 Active Bot Mutes: {mutes}\n"
            "➕ Adders: {adders}\n"
            "⏱ Uptime: {uptime}\n\n"
            "Use: /admin_users /admin_groups /admin_photos\n"
            "Owner: /clmute /transfer /addadder /rmadder /give"
        ),
        "no_users": "No users.",
        "user_list_header": "👤 USER LIST",
        "no_groups": "No groups.",
        "group_list_header": "👥 GROUP LIST",
        "no_cards": "No cards.",
        "card_list_header": "🖼 CARD LIST",
        "clmute_group_only": "❌ Use /clmute in a group.",
        "clmute_user_cleared": "✅ Bot mute cleared for user ID {user_id}.",
        "clmute_user_not_muted": "ℹ️ User ID {user_id} is not bot-muted.",
        "clmute_group_cleared": "✅ Cleared {count} bot mute(s) in this group.",
        "transfer_usage": "Usage:\n/transfer <old_user_id> <new_user_id>\n/transfer <old_user_id> + reply target user",
        "transfer_invalid_old": "❌ Invalid old user ID.",
        "transfer_target_missing": "❌ Target user missing. Use /transfer oldid newid or reply user with /transfer oldid",
        "transfer_same": "❌ Old ID and new ID are the same.",
        "transfer_no_cards": "❌ Old user has no harem/cards to transfer.",
        "transfer_success": "✅ Harem transferred successfully.\n\nOld ID: {old_id}\nNew ID: {new_id}\nUnique cards: {unique}\nTotal cards: {total}",
        "addadder_usage": "Usage: /addadder <user_id> or reply user with /addadder",
        "addadder_success": "✅ User ID {user_id} can now add/update cards.",
        "rmadder_usage": "Usage: /rmadder <user_id> or reply user with /rmadder",
        "rmadder_success": "✅ User ID {user_id} removed from adders.",
        "delete_usage": "Usage: /delete <card_id>\nExample: /delete 131",
        "delete_invalid": "❌ Invalid card ID.",
        "delete_not_found": "❌ Card ID {card_id} not found in database.",
        "delete_status_skipped": "Skipped",
        "delete_status_deleted": "Deleted",
        "delete_status_failed": "Failed: {error}",
        "delete_success": (
            "🗑 <b>CARD DELETED</b>\n\n"
            "ID: <b>{card_id}</b>\n"
            "Name: <b>{name}</b>\n"
            "Rarity: <b>{rarity}</b>\n"
            "Anime: <b>{anime}</b>\n\n"
            "Photos DB deleted: <b>{photo_deleted}</b>\n"
            "Removed from users: <b>{users_modified}</b>\n"
            "Favourite cleared: <b>{fav_modified}</b>\n"
            "Active drops cleared: <b>{drop_modified}</b>\n"
            "Bika Database message: <b>{channel_status}</b>"
        ),
        "give_usage": "Usage: /give <card_id> + reply target user",
        "give_reply_target": "❌ Reply to the target user's message.\nExample: /give 1001",
        "give_bot_account": "❌ Cannot give cards to bot accounts.",
        "give_not_found": "❌ Card ID {card_id} not found.",
        "give_caption": (
            "🎁 <b>OWNER GIVE</b>\n\n"
            "To: {target}\n"
            "Card: {emoji} <b>{name}</b>\n"
            "ID: <b>{card_id}</b>\n"
            "Anime: <b>{anime}</b>\n"
            "Qty: <b>1</b>"
        ),
    }
}
