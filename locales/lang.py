from __future__ import annotations

# Central text templates for BIKA Character Bot.
# Style rule:
# - Main bot UI text uses small-caps Unicode.
# - Titles / headers use mathematical bold Unicode.
# - Telegram commands and {format_placeholders} are intentionally kept normal.
# Keep HTML tags only in templates sent with HTML parse mode.

LANG = {'en': {'start_button_add_group': '➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ',
        'start_button_support': '💬 ꜱᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ',
        'start_button_update': '📢 ᴜᴘᴅᴀᴛᴇ ᴄʜᴀɴɴᴇʟ',
        'start_message': 'ʜᴇʟʟᴏ {mention} !\n'
                         '\n'
                         "ɪ'ᴍ <b>𝐃𝐨𝐧𝐠𝐡𝐮𝐚 𝐂𝐡𝐚𝐫𝐚𝐜𝐭𝐞𝐫 𝐁𝐨𝐭</b> .\n"
                         '\n'
                         'ᴀ ᴄᴜᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴄᴀᴛᴄʜɪɴɢ ᴀᴅᴠᴇɴᴛᴜʀᴇ. ᴀᴅᴅ ᴍᴇ ᴛᴏ ᴀ ɢʀᴏᴜᴘ, ᴄᴏʟʟᴇᴄᴛ ꜰᴀꜱᴛ, ᴀɴᴅ ʙᴜɪʟᴅ ʏᴏᴜʀ ʜᴀʀᴇᴍ.',
        'not_your_action': 'ɴᴏᴛ ʏᴏᴜʀ ᴀᴄᴛɪᴏɴ.',
        'not_allowed': 'ɴᴏᴛ ᴀʟʟᴏᴡᴇᴅ.',
        'invalid_mode': 'ɪɴᴠᴀʟɪᴅ ᴍᴏᴅᴇ.',
        'updated': 'ᴜᴘᴅᴀᴛᴇᴅ.',
        'cancelled': 'ᴄᴀɴᴄᴇʟʟᴇᴅ.',
        'failed': 'ꜰᴀɪʟᴇᴅ.',
        'unknown': 'ᴜɴᴋɴᴏᴡɴ',
        'hmode_button_default': '📺 𝐒𝐎𝐑𝐓 𝐁𝐘 𝐀𝐍𝐈𝐌𝐄',
        'hmode_button_detailed': '⛩ 𝐒𝐎𝐑𝐓 𝐁𝐘 𝐑𝐀𝐑𝐈𝐓𝐘',
        'hmode_button_reset': '🚮 𝐂𝐋𝐎𝐒𝐄',
        'hmode_intro': '❄️ <b>𝐂𝐚𝐧 𝐂𝐡𝐨𝐨𝐬𝐞 𝐇𝐨𝐰 𝐓𝐨 𝐒𝐨𝐫𝐭 𝐘𝐨𝐮𝐫 𝐇𝐚𝐫𝐞𝐦:</b>',
        'hmode_set': '✅ ʜᴀʀᴇᴍ ꜱᴏʀᴛ ᴍᴏᴅᴇ ꜱᴇᴛ ᴛᴏ {mode}.',
        'check_usage': 'ᴜꜱᴀɢᴇ: /check <card id>',
        'check_not_found': '❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪᴅ {card_id} ɴᴏᴛ ꜰᴏᴜɴᴅ.',
        'card_check_header': '<b>𝐎𝐰𝐎! 𝐂𝐡𝐞𝐜𝐤 𝐨𝐮𝐭 𝐭𝐡𝐢𝐬 𝐜𝐡𝐚𝐫𝐚𝐜𝐭𝐞𝐫!</b>',
        'rarity_line': '({emoji} <b>𝐑𝐀𝐑𝐈𝐓𝐘:</b> {rarity})',
        'caught_globally': '🌍 <b>ᴄᴀᴜɢʜᴛ ɢʟᴏʙᴀʟʟʏ:</b> {total} ᴛɪᴍᴇꜱ',
        'top10_catchers': '📈 <b>ᴛᴏᴘ 10 ᴄᴀᴛᴄʜᴇʀs ᴏғ ᴛʜɪs ᴄʜᴀʀᴀᴄᴛᴇʀ!!</b>',
        'no_catch_data': '↪ ɴᴏ ᴄᴀᴛᴄʜ ᴅᴀᴛᴀ ʏᴇᴛ',
        'inline_description': '{anime} | {rarity} | ɪᴅ: {card_id}',
        'harem_header': "📘 {name}'ꜱ ʀᴇᴄᴇɴᴛ ᴄʜᴀʀᴀᴄᴛᴇʀꜱ - ᴘᴀɢᴇ: {page}/{total_pages}",
        'harem_summary': '🎴 ᴛᴏᴛᴀʟ ᴄᴀʀᴅꜱ: {total_cards} | 📚 ᴛᴏᴛᴀʟ ꜱᴇʀɪᴇꜱ: {total_series} | 🧩 ᴍᴏᴅᴇ: {mode}',
        'harem_favourite': '💖 ꜰᴀᴠᴏᴜʀɪᴛᴇ: {name} [{card_id}]',
        'harem_no_cards': 'ɴᴏ ᴄᴀʀᴅꜱ ʏᴇᴛ.',
        'harem_no_cards_user': "ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴʏ ᴄᴀʀᴅꜱ ʏᴇᴛ.",
        'harem_no_cards_alert': 'ɴᴏ ᴄᴀʀᴅꜱ.',
        'harem_button_back': '« ʙᴀᴄᴋ',
        'harem_button_next': 'ɴᴇxᴛ »',
        'profile_header': '🎗𝐃𝐎𝐍𝐆𝐇𝐔𝐀 𝐂𝐀𝐓𝐂𝐇𝐄𝐑 𝐏𝐑𝐎𝐅𝐈𝐋𝐄🎗',
        'profile_user': '👤 ᴜꜱᴇʀ: {username}',
        'profile_user_id': '🆔 ᴜꜱᴇʀ ɪᴅ: {user_id}',
        'profile_total_character': '⚡ ᴛᴏᴛᴀʟ ᴄʜᴀʀᴀᴄᴛᴇʀ: {total_owned} ({unique_owned})',
        'profile_harem': '🫧 ʜᴀʀᴇᴍ: {unique_owned}/{total_photo_count} ({percent:.3f}%)',
        'profile_level': 'ℹ️ ᴇxᴘᴇʀɪᴇɴᴄᴇ ʟᴇᴠᴇʟ: {level}',
        'profile_progress': '📈 ᴘʀᴏɢʀᴇꜱꜱ ʙᴀʀ: {bar}',
        'profile_favourite': '💖 ꜰᴀᴠᴏᴜʀɪᴛᴇ: {name} [{card_id}]',
        'profile_favourite_not_set': '💖 ꜰᴀᴠᴏᴜʀɪᴛᴇ: ɴᴏᴛ ꜱᴇᴛ',
        'profile_rarity_line': '{emoji} ʀᴀʀɪᴛʏ {rarity}: {unique} ({total})',
        'fav_not_set': '💖 ꜰᴀᴠᴏᴜʀɪᴛᴇ ɪꜱ ɴᴏᴛ ꜱᴇᴛ.\nᴜꜱᴇ: /fav <card id>',
        'fav_current_caption': '💖 ʏᴏᴜʀ ꜰᴀᴠᴏᴜʀɪᴛᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ\n{emoji} {name} [{card_id}]\nᴀɴɪᴍᴇ: {anime}',
        'fav_missing_collection': 'ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴅᴏᴇꜱ ɴᴏᴛ ᴇxɪꜱᴛ ɪɴ ʏᴏᴜʀ ᴄᴏʟʟᴇᴄᴛɪᴏɴ.',
        'fav_confirm': 'ᴅᴏ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ꜱᴇᴛ ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴀꜱ ʏᴏᴜʀ ꜰᴀᴠᴏᴜʀɪᴛᴇ?\n↪ {name} ({anime})',
        'fav_button_yes': '✅ ʏᴇꜱ',
        'fav_button_no': '❌ ɴᴏ',
        'fav_card_missing': 'ᴄᴀʀᴅ ᴍɪꜱꜱɪɴɢ.',
        'fav_set': '💖 ꜰᴀᴠᴏᴜʀɪᴛᴇ ꜱᴇᴛ ᴛᴏ {name} [{card_id}]',
        'fav_updated': 'ꜰᴀᴠᴏᴜʀɪᴛᴇ ᴜᴘᴅᴀᴛᴇᴅ.',
        'fav_cancelled': '❌ ꜰᴀᴠᴏᴜʀɪᴛᴇ ᴜᴘᴅᴀᴛᴇ ᴄᴀɴᴄᴇʟʟᴇᴅ.',
        'gift_reply_target': "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ'ꜱ ᴍᴇꜱꜱᴀɢᴇ.\nᴇxᴀᴍᴘʟᴇ: .ɢɪꜰᴛ 1001",
        'gift_usage': 'ᴜꜱᴀɢᴇ: .ɢɪꜰᴛ <card id> [ǫᴛʏ]',
        'gift_self': "❌ ʏᴏᴜ ᴄᴀɴ'ᴛ ɢɪꜰᴛ ᴛᴏ ʏᴏᴜʀꜱᴇʟꜰ.",
        'gift_card_not_found_inventory': '❌ ᴄᴀʀᴅ ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ʏᴏᴜʀ ɪɴᴠᴇɴᴛᴏʀʏ.',
        'gift_not_enough': '❌ ɴᴏᴛ ᴇɴᴏᴜɢʜ ǫᴜᴀɴᴛɪᴛʏ.',
        'gift_preview': '🎁 <b>𝐆𝐈𝐅𝐓 𝐏𝐑𝐄𝐕𝐈𝐄𝐖</b>\n'
                        '\n'
                        'ꜰʀᴏᴍ: {sender}\n'
                        'ᴛᴏ: {receiver}\n'
                        'ᴄᴀʀᴅ: {emoji} {name}\n'
                        'ɪᴅ: {card_id}\n'
                        'ᴀɴɪᴍᴇ: {anime}\n'
                        'ǫᴛʏ: {qty}\n'
                        '\n'
                        'ᴀʀᴇ ʏᴏᴜ ꜱᴜʀᴇ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ꜱᴇɴᴅ ᴛʜɪꜱ ᴄᴀʀᴅ?',
        'gift_button_confirm': '✅ ᴄᴏɴꜰɪʀᴍ',
        'gift_button_cancel': '❌ ᴄᴀɴᴄᴇʟ',
        'gift_not_your': 'ɴᴏᴛ ʏᴏᴜʀ ɢɪꜰᴛ ᴀᴄᴛɪᴏɴ.',
        'gift_success': '✅ ɢɪꜰᴛ ꜱᴇɴᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.\n\nᴄᴀʀᴅ: {emoji} {name}\nɪᴅ: {card_id}\nǫᴛʏ: {qty}',
        'gift_confirmed': 'ɢɪꜰᴛ ᴄᴏɴꜰɪʀᴍᴇᴅ.',
        'gift_not_your_cancel': 'ɴᴏᴛ ʏᴏᴜʀ ᴄᴀɴᴄᴇʟ ᴀᴄᴛɪᴏɴ.',
        'gift_cancelled': '❌ ɢɪꜰᴛ ᴄᴀɴᴄᴇʟʟᴇᴅ.',
        'rank_no_group': 'ɴᴏ ɢʀᴏᴜᴘ ᴄᴀᴛᴄʜ ʀᴀɴᴋɪɴɢ ʏᴇᴛ.',
        'rank_group_header': '🏆 <b>𝐓𝐎𝐏 𝐆𝐑𝐎𝐔𝐏 𝐑𝐀𝐍𝐊𝐈𝐍𝐆</b>',
        'rank_group_subtitle': '<b>𝐁𝐲 /dao 𝐜𝐚𝐭𝐜𝐡𝐞𝐬 𝐫𝐚𝐧𝐤𝐢𝐧𝐠</b>',
        'rank_group_row': '{rank} {group} — <b>{count}</b> ᴄᴀᴛᴄʜᴇꜱ',
        'rank_no_global': 'ɴᴏ ɢʟᴏʙᴀʟ ʜᴀʀᴇᴍ ʀᴀɴᴋɪɴɢ ʏᴇᴛ.',
        'rank_global_header': '🌍 <b>𝐆𝐋𝐎𝐁𝐀𝐋 𝐓𝐎𝐏 𝟏𝟎 𝐔𝐒𝐄𝐑𝐒</b>',
        'rank_global_subtitle': '<b>𝐁𝐲 𝐭𝐨𝐭𝐚𝐥 𝐡𝐚𝐫𝐞𝐦 𝐜𝐡𝐚𝐫𝐚𝐜𝐭𝐞𝐫𝐬</b>',
        'rank_global_row': '{rank} {user} — <b>{total}</b> ᴛᴏᴛᴀʟ | {unique} ᴜɴɪǫᴜᴇ',
        'rank_no_today': 'ɴᴏ ᴄᴀᴛᴄʜᴇꜱ ʏᴇᴛ ᴛᴏᴅᴀʏ.\nᴅᴀᴛᴇ: {date} ({timezone})',
        'rank_today_header': '📅 <b>𝐓𝐎𝐃𝐀𝐘 𝐆𝐋𝐎𝐁𝐀𝐋 𝐓𝐎𝐏 𝟏𝟎</b>',
        'rank_today_date': 'ᴅᴀᴛᴇ: <b>{date}</b> ({timezone})',
        'rank_today_subtitle': '<b>𝐁𝐲 /dao 𝐜𝐚𝐭𝐜𝐡𝐞𝐬 𝐭𝐨𝐝𝐚𝐲</b>',
        'rank_today_row': '{rank} {user} — <b>{count}</b> ᴄᴀᴛᴄʜᴇꜱ',
        'mylimit': '🎯 ᴅᴀɪʟʏ ᴄᴀᴛᴄʜ ʟɪᴍɪᴛ\n\nᴅᴀᴛᴇ: {date} ({timezone})\nᴜꜱᴇᴅ: {used}/{limit}\nʀᴇᴍᴀɪɴɪɴɢ: {remaining}',
        'bot_muted': '🤐 {name}, ʏᴏᴜ ꜱᴇɴᴛ ᴛᴏᴏ ᴍᴀɴʏ ᴍᴇꜱꜱᴀɢᴇꜱ ɪɴ ᴀ ʀᴏᴡ. ʙᴏᴛ ᴡɪʟʟ ɪɢɴᴏʀᴇ ʏᴏᴜ ꜰᴏʀ {minutes} ᴍɪɴᴜᴛᴇꜱ.',
        'pre_spawn_captcha': '🧩 <b>𝐇𝐈𝐆𝐇 𝐑𝐀𝐑𝐈𝐓𝐘 𝐂𝐀𝐏𝐓𝐂𝐇𝐀</b>\n'
                             '\n'
                             '{emoji} <b>{rarity}</b> ᴄᴀʀᴅ ɪꜱ ᴛʀʏɪɴɢ ᴛᴏ ꜱᴘᴀᴡɴ ɪɴ <b>{group_name}</b>.\n'
                             '\n'
                             'ꜱᴏʟᴠᴇ ᴛʜɪꜱ ᴄᴀᴘᴛᴄʜᴀ ᴡɪᴛʜɪɴ <b>{seconds}𝐬</b>.\n'
                             '✅ ᴄᴏʀʀᴇᴄᴛ ᴀɴꜱᴡᴇʀ = ᴄʜᴀʀᴀᴄᴛᴇʀ ᴡɪʟʟ ꜱᴘᴀᴡɴ.\n'
                             '❌ ᴡʀᴏɴɢ ᴀɴꜱᴡᴇʀ ᴏʀ ᴛɪᴍᴇᴏᴜᴛ = ᴛʜɪꜱ ᴅʀᴏᴘ ᴡɪʟʟ ʙᴇ ʟᴏꜱᴛ.\n'
                             '\n'
                             'ǫᴜᴇꜱᴛɪᴏɴ: <b>{question}</b>',
        'pre_spawn_timeout': '⌛ <b>𝐂𝐀𝐏𝐓𝐂𝐇𝐀 𝐓𝐈𝐌𝐄𝐎𝐔𝐓</b>\n'
                             '\n'
                             '120 ꜱᴇᴄᴏɴᴅꜱ ꜰɪɴɪꜱʜᴇᴅ. ᴛʜɪꜱ ꜱᴄʜᴇᴅᴜʟᴇᴅ ʜɪɢʜ-ʀᴀʀɪᴛʏ ꜱᴘᴀᴡɴ ʜᴀꜱ ʙᴇᴇɴ ʟᴏꜱᴛ.',
        'captcha_invalid': 'ɪɴᴠᴀʟɪᴅ ᴄᴀᴘᴛᴄʜᴀ.',
        'captcha_finished': 'ᴄᴀᴘᴛᴄʜᴀ ᴀʟʀᴇᴀᴅʏ ꜰɪɴɪꜱʜᴇᴅ.',
        'pre_spawn_wrong': '❌ <b>𝐖𝐑𝐎𝐍𝐆 𝐂𝐀𝐏𝐓𝐂𝐇𝐀</b>\n\nᴛʜɪꜱ ꜱᴄʜᴇᴅᴜʟᴇᴅ ʜɪɢʜ-ʀᴀʀɪᴛʏ ꜱᴘᴀᴡɴ ʜᴀꜱ ʙᴇᴇɴ ʟᴏꜱᴛ.',
        'pre_spawn_wrong_alert': 'ᴡʀᴏɴɢ. ꜱᴘᴀᴡɴ ʟᴏꜱᴛ.',
        'pre_spawn_solved': '✅ <b>𝐂𝐀𝐏𝐓𝐂𝐇𝐀 𝐒𝐎𝐋𝐕𝐄𝐃</b>\n\nʜɪɢʜ-ʀᴀʀɪᴛʏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪꜱ ꜱᴘᴀᴡɴɪɴɢ ɴᴏᴡ!',
        'solved': 'ꜱᴏʟᴠᴇᴅ.',
        'spawn_no_card': '❌ ɴᴏ ᴄᴀʀᴅ ꜰᴏᴜɴᴅ ꜰᴏʀ ᴛʜɪꜱ ꜱᴄʜᴇᴅᴜʟᴇᴅ ʀᴀʀɪᴛʏ. ꜱᴘᴀᴡɴ ʟᴏꜱᴛ.',
        'spawn_caption': '{emoji} ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀꜱ ꜱᴘᴀᴡɴᴇᴅ ɪɴ {group_name} .\n'
                         '\n'
                         'ᴛᴏ ᴏᴡɴ ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ, ꜱᴇɴᴅ ᴛʜᴇ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ ǫᴜɪᴄᴋʟʏ ᴜꜱɪɴɢ /bika ɴᴀᴍᴇ .',
        'claim_captcha_timeout': '⌛ <b>𝐂𝐀𝐏𝐓𝐂𝐇𝐀 𝐓𝐈𝐌𝐄𝐎𝐔𝐓</b>\n'
                                 '\n'
                                 '120 ꜱᴇᴄᴏɴᴅꜱ ꜰɪɴɪꜱʜᴇᴅ. ᴛʜɪꜱ ʜɪɢʜ-ʀᴀʀɪᴛʏ ᴄᴀʀᴅ ʜᴀꜱ ʙᴇᴇɴ ʟᴏꜱᴛ.',
        'claim_success': '🎉 <b>𝐘𝐎𝐔 𝐆𝐎𝐓 𝐀 𝐍𝐄𝐖 𝐂𝐇𝐀𝐑𝐀𝐂𝐓𝐄𝐑!</b>\n'
                         '\n'
                         '👤 ᴄʟᴀɪᴍᴇᴅ ʙʏ: {claimer}\n'
                         '{emoji} ɴᴀᴍᴇ: <b>{name}</b>\n'
                         '🆔 ɪᴅ: <b>{card_id}</b>\n'
                         '🏷 ʀᴀʀɪᴛʏ: <b>{rarity}</b>\n'
                         '🌴 ᴀɴɪᴍᴇ: <b>{anime}</b>\n'
                         '\n'
                         '❄️ ᴄʜᴇᴄᴋ ʏᴏᴜʀ /harem !',
        'daily_limit': '❌ ᴅᴀɪʟʏ ᴄᴀᴛᴄʜ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ.\n'
                       'ᴍʏᴀɴᴍᴀʀ/Yangon ᴅᴀᴛᴇ: {date}\n'
                       'ᴜꜱᴇᴅ: {used}/{limit}\n'
                       'ʀᴇᴍᴀɪɴɪɴɢ: {remaining}',
        'claim_captcha_active': '⏳ ᴄᴀᴘᴛᴄʜᴀ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴄᴛɪᴠᴇ ꜰᴏʀ ᴛʜɪꜱ ᴅʀᴏᴘ. ᴡᴀɪᴛ ꜰᴏʀ ᴛʜᴇ ʀᴇꜱᴜʟᴛ.',
        'character_unavailable': '❌ ᴛʜɪꜱ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪꜱ ɴᴏ ʟᴏɴɢᴇʀ ᴀᴠᴀɪʟᴀʙʟᴇ.',
        'claim_captcha_required': '🧩 <b>𝐂𝐀𝐏𝐓𝐂𝐇𝐀 𝐒𝐎𝐋𝐕𝐄 𝐑𝐄𝐐𝐔𝐈𝐑𝐄𝐃</b>\n'
                                  '\n'
                                  '{emoji} <b>{rarity}</b> ᴀɴᴅ ᴀʙᴏᴠᴇ ᴍᴜꜱᴛ ᴘᴀꜱꜱ ᴄᴀᴘᴛᴄʜᴀ.\n'
                                  '👤 ᴘʟᴀʏᴇʀ: {player}\n'
                                  '🎴 ᴄᴀʀᴅ: <b>{card_name}</b> [{card_id}]\n'
                                  '\n'
                                  'ꜱᴏʟᴠᴇ ᴡɪᴛʜɪɴ <b>{seconds}𝐬</b> ᴏʀ ᴛʜɪꜱ ᴄᴀʀᴅ ᴡɪʟʟ ʙᴇ ʟᴏꜱᴛ.\n'
                                  'ǫᴜᴇꜱᴛɪᴏɴ: <b>{question}</b>',
        'no_character_available': '❌ ɴᴏ ᴄʜᴀʀᴀᴄᴛᴇʀ ɪꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ʀɪɢʜᴛ ɴᴏᴡ.',
        'already_caught': '❌ <b>𝐂𝐇𝐀𝐑𝐀𝐂𝐓𝐄𝐑 𝐀𝐋𝐑𝐄𝐀𝐃𝐘 𝐂𝐀𝐔𝐆𝐇𝐓</b>\n'
                          '\n'
                          'ᴄᴀᴜɢʜᴛ ʙʏ: {caught_by}\n'
                          '\n'
                          '🥤 ᴡᴀɪᴛ ꜰᴏʀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ ᴛᴏ ꜱᴘᴀᴡɴ.',
        'claim_high_captcha_active': '⏳ ᴄᴀᴘᴛᴄʜᴀ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴄᴛɪᴠᴇ ꜰᴏʀ ᴛʜɪꜱ ʜɪɢʜ-ʀᴀʀɪᴛʏ ᴅʀᴏᴘ.',
        'wrong_name': '❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ {guess} ɪꜱ ɪɴᴄᴏʀʀᴇᴄᴛ\n\n{arrow} ɪꜱ ꜱᴛɪʟʟ ᴀᴠᴀɪʟᴀʙʟᴇ.',
        'wrong_name_empty': '❌ ᴄʜᴀʀᴀᴄᴛᴇʀ ɴᴀᴍᴇ ɪꜱ ɪɴᴄᴏʀʀᴇᴄᴛ\n\n{arrow} ɪꜱ ꜱᴛɪʟʟ ᴀᴠᴀɪʟᴀʙʟᴇ.',
        'drop_data_missing': '❌ ᴅʀᴏᴘ ᴅᴀᴛᴀ ᴍɪꜱꜱɪɴɢ.',
        'captcha_not_for_you': 'ᴛʜɪꜱ ᴄᴀᴘᴛᴄʜᴀ ɪꜱ ɴᴏᴛ ꜰᴏʀ ʏᴏᴜ.',
        'captcha_expired': 'ᴇxᴘɪʀᴇᴅ.',
        'claim_wrong_captcha': '❌ <b>𝐖𝐑𝐎𝐍𝐆 𝐂𝐀𝐏𝐓𝐂𝐇𝐀</b>\n\nᴛʜɪꜱ ʜɪɢʜ-ʀᴀʀɪᴛʏ ᴄᴀʀᴅ ʜᴀꜱ ʙᴇᴇɴ ʟᴏꜱᴛ.',
        'claim_wrong_card_lost': 'ᴡʀᴏɴɢ. ᴄᴀʀᴅ ʟᴏꜱᴛ.',
        'daily_limit_card_lost': '❌ <b>𝐃𝐀𝐈𝐋𝐘 𝐋𝐈𝐌𝐈𝐓 𝐑𝐄𝐀𝐂𝐇𝐄𝐃</b>\n\nᴜꜱᴇᴅ: {used}/{limit}. ᴛʜɪꜱ ᴄᴀʀᴅ ʜᴀꜱ ʙᴇᴇɴ ʟᴏꜱᴛ.',
        'daily_limit_reached_alert': 'ᴅᴀɪʟʏ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ.',
        'card_no_longer_available': 'ᴛʜɪꜱ ᴄᴀʀᴅ ɪꜱ ɴᴏ ʟᴏɴɢᴇʀ ᴀᴠᴀɪʟᴀʙʟᴇ.',
        'claim_captcha_solved': '✅ <b>𝐂𝐀𝐏𝐓𝐂𝐇𝐀 𝐒𝐎𝐋𝐕𝐄𝐃</b>\n\n{user} ɢᴏᴛ ᴛʜᴇ ᴄᴀʀᴅ.',
        'captcha_solved_alert': 'ᴄᴀᴘᴛᴄʜᴀ ꜱᴏʟᴠᴇᴅ.',
        'group_admin_only': '❌ ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ ᴏɴʟʏ.',
        'changetime_usage': 'ᴜꜱᴀɢᴇ: /changetime <number>\n'
                            'ɢʀᴏᴜᴘ ᴀᴅᴍɪɴ: {admin_min}-{admin_max}\n'
                            'ᴏᴡɴᴇʀ: {owner_min}-{owner_max}',
        'changetime_range': '❌ ᴄʜᴀɴɢᴇᴛɪᴍᴇ ᴍᴜꜱᴛ ʙᴇ ʙᴇᴛᴡᴇᴇɴ {min_v} ᴀɴᴅ {max_v}.',
        'changetime_updated': '✅ ᴄʜᴀɴɢᴇᴛɪᴍᴇ ᴜᴘᴅᴀᴛᴇᴅ ᴛᴏ {value} ᴍᴇꜱꜱᴀɢᴇꜱ.',
        'admin_dashboard': '⚙️ 𝐃𝐎𝐍𝐆𝐇𝐔𝐀 𝐀𝐃𝐌𝐈𝐍 𝐃𝐀𝐒𝐇𝐁𝐎𝐀𝐑𝐃\n'
                           '\n'
                           '👤 ᴜꜱᴇʀꜱ: {users}\n'
                           '👥 ɢʀᴏᴜᴘꜱ: {groups}\n'
                           '🖼 ᴄᴀʀᴅꜱ: {cards}\n'
                           '🎁 ᴛʀᴀɴꜱꜰᴇʀꜱ: {transfers}\n'
                           '🤐 ᴀᴄᴛɪᴠᴇ ʙᴏᴛ ᴍᴜᴛᴇꜱ: {mutes}\n'
                           '➕ ᴀᴅᴅᴇʀꜱ: {adders}\n'
                           '⏱ ᴜᴘᴛɪᴍᴇ: {uptime}\n'
                           '\n'
                           'ᴏᴡɴᴇʀ: /clmute /transfer /addadder /rmadder /give',
        'no_users': 'ɴᴏ ᴜꜱᴇʀꜱ.',
        'user_list_header': '👤 𝐔𝐒𝐄𝐑 𝐋𝐈𝐒𝐓',
        'no_groups': 'ɴᴏ ɢʀᴏᴜᴘꜱ.',
        'group_list_header': '👥 𝐆𝐑𝐎𝐔𝐏 𝐋𝐈𝐒𝐓',
        'no_cards': 'ɴᴏ ᴄᴀʀᴅꜱ.',
        'card_list_header': '🖼 𝐂𝐀𝐑𝐃 𝐋𝐈𝐒𝐓',
        'clmute_group_only': '❌ ᴜꜱᴇ /clmute ɪɴ ᴀ ɢʀᴏᴜᴘ.',
        'clmute_user_cleared': '✅ ʙᴏᴛ ᴍᴜᴛᴇ ᴄʟᴇᴀʀᴇᴅ ꜰᴏʀ ᴜꜱᴇʀ ɪᴅ {user_id}.',
        'clmute_user_not_muted': 'ℹ️ ᴜꜱᴇʀ ɪᴅ {user_id} ɪꜱ ɴᴏᴛ ʙᴏᴛ-ᴍᴜᴛᴇᴅ.',
        'clmute_group_cleared': '✅ ᴄʟᴇᴀʀᴇᴅ {count} ʙᴏᴛ ᴍᴜᴛᴇ(ꜱ) ɪɴ ᴛʜɪꜱ ɢʀᴏᴜᴘ.',
        'transfer_usage': 'ᴜꜱᴀɢᴇ:\n/transfer <old_user_id> <new_user_id>\n/transfer <old_user_id> + ʀᴇᴘʟʏ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ',
        'transfer_invalid_old': '❌ ɪɴᴠᴀʟɪᴅ ᴏʟᴅ ᴜꜱᴇʀ ɪᴅ.',
        'transfer_target_missing': '❌ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ ᴍɪꜱꜱɪɴɢ. ᴜꜱᴇ /transfer ᴏʟᴅɪᴅ ɴᴇᴡɪᴅ ᴏʀ ʀᴇᴘʟʏ ᴜꜱᴇʀ ᴡɪᴛʜ /transfer '
                                   'ᴏʟᴅɪᴅ',
        'transfer_same': '❌ ᴏʟᴅ ɪᴅ ᴀɴᴅ ɴᴇᴡ ɪᴅ ᴀʀᴇ ᴛʜᴇ ꜱᴀᴍᴇ.',
        'transfer_no_cards': '❌ ᴏʟᴅ ᴜꜱᴇʀ ʜᴀꜱ ɴᴏ ʜᴀʀᴇᴍ/cards ᴛᴏ ᴛʀᴀɴꜱꜰᴇʀ.',
        'transfer_success': '✅ ʜᴀʀᴇᴍ ᴛʀᴀɴꜱꜰᴇʀʀᴇᴅ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ.\n'
                            '\n'
                            'ᴏʟᴅ ɪᴅ: {old_id}\n'
                            'ɴᴇᴡ ɪᴅ: {new_id}\n'
                            'ᴜɴɪǫᴜᴇ ᴄᴀʀᴅꜱ: {unique}\n'
                            'ᴛᴏᴛᴀʟ ᴄᴀʀᴅꜱ: {total}',
        'addadder_usage': 'ᴜꜱᴀɢᴇ: /addadder <user_id> ᴏʀ ʀᴇᴘʟʏ ᴜꜱᴇʀ ᴡɪᴛʜ /addadder',
        'addadder_success': '✅ ᴜꜱᴇʀ ɪᴅ {user_id} ᴄᴀɴ ɴᴏᴡ ᴀᴅᴅ/update ᴄᴀʀᴅꜱ.',
        'rmadder_usage': 'ᴜꜱᴀɢᴇ: /rmadder <user_id> ᴏʀ ʀᴇᴘʟʏ ᴜꜱᴇʀ ᴡɪᴛʜ /rmadder',
        'rmadder_success': '✅ ᴜꜱᴇʀ ɪᴅ {user_id} ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ᴀᴅᴅᴇʀꜱ.',
        'delete_usage': 'ᴜꜱᴀɢᴇ: /delete <card_id>\nᴇxᴀᴍᴘʟᴇ: /delete 131',
        'delete_invalid': '❌ ɪɴᴠᴀʟɪᴅ ᴄᴀʀᴅ ɪᴅ.',
        'delete_not_found': '❌ ᴄᴀʀᴅ ɪᴅ {card_id} ɴᴏᴛ ꜰᴏᴜɴᴅ ɪɴ ᴅᴀᴛᴀʙᴀꜱᴇ.',
        'delete_status_skipped': 'ꜱᴋɪᴘᴘᴇᴅ',
        'delete_status_deleted': 'ᴅᴇʟᴇᴛᴇᴅ',
        'delete_status_failed': 'ꜰᴀɪʟᴇᴅ: {error}',
        'delete_success': '🗑 <b>𝐂𝐀𝐑𝐃 𝐃𝐄𝐋𝐄𝐓𝐄𝐃</b>\n'
                          '\n'
                          'ɪᴅ: <b>{card_id}</b>\n'
                          'ɴᴀᴍᴇ: <b>{name}</b>\n'
                          'ʀᴀʀɪᴛʏ: <b>{rarity}</b>\n'
                          'ᴀɴɪᴍᴇ: <b>{anime}</b>\n'
                          '\n'
                          'ᴘʜᴏᴛᴏꜱ ᴅʙ ᴅᴇʟᴇᴛᴇᴅ: <b>{photo_deleted}</b>\n'
                          'ʀᴇᴍᴏᴠᴇᴅ ꜰʀᴏᴍ ᴜꜱᴇʀꜱ: <b>{users_modified}</b>\n'
                          'ꜰᴀᴠᴏᴜʀɪᴛᴇ ᴄʟᴇᴀʀᴇᴅ: <b>{fav_modified}</b>\n'
                          'ᴀᴄᴛɪᴠᴇ ᴅʀᴏᴘꜱ ᴄʟᴇᴀʀᴇᴅ: <b>{drop_modified}</b>\n'
                          'ʙɪᴋᴀ ᴅᴀᴛᴀʙᴀꜱᴇ ᴍᴇꜱꜱᴀɢᴇ: <b>{channel_status}</b>',
        'give_usage': 'ᴜꜱᴀɢᴇ: /give <card_id> + ʀᴇᴘʟʏ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ',
        'give_reply_target': "❌ ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ ᴛᴀʀɢᴇᴛ ᴜꜱᴇʀ'ꜱ ᴍᴇꜱꜱᴀɢᴇ.\nᴇxᴀᴍᴘʟᴇ: /give 1001",
        'give_bot_account': '❌ ᴄᴀɴɴᴏᴛ ɢɪᴠᴇ ᴄᴀʀᴅꜱ ᴛᴏ ʙᴏᴛ ᴀᴄᴄᴏᴜɴᴛꜱ.',
        'give_not_found': '❌ ᴄᴀʀᴅ ɪᴅ {card_id} ɴᴏᴛ ꜰᴏᴜɴᴅ.',
        'give_caption': '🎁 <b>𝐎𝐖𝐍𝐄𝐑 𝐆𝐈𝐕𝐄</b>\n'
                        '\n'
                        'ᴛᴏ: {target}\n'
                        'ᴄᴀʀᴅ: {emoji} <b>{name}</b>\n'
                        'ɪᴅ: <b>{card_id}</b>\n'
                        'ᴀɴɪᴍᴇ: <b>{anime}</b>\n'
                        'ǫᴛʏ: <b>1</b>',
        'harem_inline_button': '⛩ 𝐂𝐇𝐀𝐑𝐀𝐂𝐓𝐄𝐑𝐒',
        'hmode_choose_sort': '❄️ <b>𝐂𝐚𝐧 𝐂𝐡𝐨𝐨𝐬𝐞 𝐇𝐨𝐰 𝐓𝐨 𝐒𝐨𝐫𝐭 𝐘𝐨𝐮𝐫 𝐇𝐚𝐫𝐞𝐦:</b>',
        'hmode_sort_by_rarity': '💮 𝐒𝐎𝐑𝐓 𝐁𝐘 𝐑𝐀𝐑𝐈𝐓𝐘',
        'hmode_sort_by_anime': '📘 𝐒𝐎𝐑𝐓 𝐁𝐘 𝐀𝐍𝐈𝐌𝐄',
        'hmode_close': '🚮 𝐂𝐋𝐎𝐒𝐄',
        'hmode_back': '⬅️ 𝐁𝐀𝐂𝐊',
        'hmode_choose_rarity': '❄️ <b>𝐂𝐇𝐎𝐎𝐒𝐄 𝐘𝐎𝐔𝐑 𝐏𝐑𝐄𝐅𝐅𝐄𝐑𝐄𝐃 𝐑𝐀𝐑𝐈𝐓𝐘</b>',
        'hmode_rarity_button': '{emoji} 𝐑𝐀𝐑𝐈𝐓𝐘: {rarity}',
        'hmode_set_anime': '✅ ʜᴀʀᴇᴍ ꜱᴏʀᴛ ᴍᴏᴅᴇ ꜱᴇᴛ ᴛᴏ <b>𝐀𝐍𝐈𝐌𝐄</b>.\n\nᴜꜱᴇ /harem ᴛᴏ ᴠɪᴇᴡ ʏᴏᴜʀ ᴄᴀʀᴅꜱ.',
        'hmode_set_rarity': '✅ ʜᴀʀᴇᴍ ꜱᴏʀᴛ ᴍᴏᴅᴇ ꜱᴇᴛ ᴛᴏ {emoji} <b>{rarity}</b>.\n\nᴜꜱᴇ /harem ᴛᴏ ᴠɪᴇᴡ ᴏɴʟʏ ᴛʜɪꜱ ʀᴀʀɪᴛʏ.',
        'harem_summary_anime': '🎴 ᴛᴏᴛᴀʟ ᴄᴀʀᴅꜱ: {total_cards} | 📚 ᴛᴏᴛᴀʟ ꜱᴇʀɪᴇꜱ: {total_series} | 📘 ᴍᴏᴅᴇ: 𝐀𝐍𝐈𝐌𝐄',
        'harem_summary_rarity': '{emoji} ʀᴀʀɪᴛʏ: <b>{rarity}</b> | 🎴 ꜱʜᴏᴡɪɴɢ: {shown_cards}/{total_cards} | 📚 ꜱᴇʀɪᴇꜱ: '
                                '{total_series}',
        'harem_no_rarity_cards': '❌ ʏᴏᴜ ᴅᴏ ɴᴏᴛ ʜᴀᴠᴇ {emoji} <b>{rarity}</b> ᴄᴀʀᴅꜱ ʏᴇᴛ.'}}
