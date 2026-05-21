# BIKA Character Catcher Bot — Python Async Version

A production-ready Telegram character catcher bot built with `python-telegram-bot` and MongoDB/Motor.

## Main features

- Group message counter based random character drops
- Per-group `/changetime`
  - Group admins: 100 to 999
  - Owner: 1 to 3000
- Default changetime: 100
- `/bika <name>` first correct claimer wins the spawned card
- `/harem` and `.harem` card list with pagination buttons
- `/fav <id>` and `.fav <id>` favourite card support
- `/profile`, `/check <id>`
- `.gift <id> [qty]` or `/gift <id> [qty]` with confirm/cancel buttons
- Owner/adders `/add` photo support in DM with Bika Database private channel archive
- No approve system: bot works immediately after being added to a group
- Sends a log to `GROUP_LOG_CHANNEL_ID` when bot is added to a new group
- Anti-spam: if one user sends 6 messages in a row, bot ignores that user for 10 minutes in that group

- Owner `/clmute` to clear bot-internal mutes
- Owner `/transfer oldid newid` or `/transfer oldid` + reply user to move a full harem
- Owner `/addadder` and `/rmadder` to allow/remove non-owner card adders
- Owner `/give cardid` + reply user to add one card directly
- `/topgroup` top 10 groups by `/bika` catch count
- `/gtop` global top 10 users by total harem character count
- `/todaygtop` Myanmar/Yangon daily top 10 users by `/bika` catch count
- Each user can catch only 25 cards per Myanmar/Yangon day by default
- `/mylimit` checks used and remaining daily catch slots

## Important Telegram note

Telegram Bot API does **not** allow bots to choose real inline button background colors. This project uses emoji labels like 🟢 🔴 🟦 🟩 to make buttons visually colored. Regular Unicode emoji are supported. Real custom premium emoji require Telegram custom emoji IDs and message entities; this starter keeps it simple and stable.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:

```env
BOT_TOKEN=your_bot_token
MONGODB_URI=your_mongodb_uri
OWNER_ID=your_telegram_user_id
BOT_USERNAME=YourBotUsername
```

Run:

```bash
python bot.py
```

Health check:

```text
http://localhost:8080/
```

## Add cards / Bika Database channel

Create a private channel named **Bika Database**, add the bot as admin, then set `CARD_DATABASE_CHANNEL_ID` in `.env`. Every `/add` will post the card media to that private channel first, then save `fileId`, `fileUniqueId`, `storageChatId`, and `storageMessageId` in MongoDB.

New card with auto ID from 1 upward:

```text
/add Yelan | Legendary | Genshin Impact
```

Update/save a specific ID without changing the ID:

```text
/add 2 | Yelan | Legendary | Genshin Impact
```

If ID 400 already exists as the latest ID and you update ID 2, the card remains ID 2. The next auto ID continues from the latest counter.

Bot replies and channel captions show `Saved` for new cards and `Update` for existing card edits.

Allowed rarities:

```text
Supreme, Cataphract, CrossVerse, Divine, Mystical, Legendary, Rare, Uncommon, Common
```

## Change drop count

Group admin:

```text
/changetime 150
```

Owner can set 1 to 3000:

```text
/changetime 1
```


## Owner tools

Clear bot mutes in current group:

```text
/clmute
/clmute <user_id>
/clmute + reply user
```

Transfer a whole harem from one user ID to another:

```text
/transfer old_user_id new_user_id
/transfer old_user_id + reply target user
```

Allow or remove extra users who can add cards by DM photo captions:

```text
/addadder <user_id>
/addadder + reply user
/rmadder <user_id>
/rmadder + reply user
```

Give a card directly to a replied user. This adds `x1` to the target user's harem and sends the card media in chat:

```text
/give <card_id> + reply user
```

Gift count logic: `.gift <cardid>` or `/gift <cardid>` removes `x1` from sender and adds `x1` to receiver. If sender has `x3`, sender becomes `x2`; if receiver already has `x1`, receiver becomes `x2`.

## Deploy notes

This project runs in polling mode and also opens a small HTTP health server on `PORT`. For PM2:

```bash
pm2 start bot.py --name bika-python --interpreter python3
```

For Render/Railway, use:

```bash
python bot.py
```

## Project structure

```text
bika_character_bot/
├─ bot.py
├─ config.py
├─ requirements.txt
├─ .env.example
├─ database/
├─ handlers/
├─ utils/
└─ web/
```

## Rankings and daily catch limit

Top groups by all-time `/bika` catches:

```text
/topgroup
```

Global top 10 users by total harem character count:

```text
/gtop
```

Today global top 10 users by `/bika` catches using Myanmar/Yangon date:

```text
/todaygtop
```

Check your daily catch quota:

```text
/mylimit
```

Default daily catch limit is 25 cards per user per Myanmar/Yangon day. You can change it in `.env`:

```env
CLAIM_DAILY_LIMIT=25
CLAIM_TIMEZONE=Asia/Yangon
```


## Inline character search

Enable inline mode in BotFather first:

```text
/setinline
```

Set the placeholder to something like:

```text
Search characters...
```

Usage:

```text
@YourBotUsername
@YourBotUsername Yelan
```

Empty inline query shows all database cards from ID 1 upward. Non-empty query searches the `photos` collection by `normalizedName` and returns every matching database card through Telegram inline pagination. Telegram allows only up to 50 results per inline answer, so the bot sends 50 per page and keeps loading more with `next_offset`; there is no bot-side total database limit. Selecting a result sends the character media with its ID, name, anime, and rarity caption.


## START PAGE BUTTONS

Set these in `.env` to customize `/start` buttons:

```env
ADD_TO_GROUP_URL=
SUPPORT_GROUP_URL=https://t.me/YourSupportGroup
UPDATE_CHANNEL_URL=https://t.me/YourUpdateChannel
```

If `ADD_TO_GROUP_URL` is empty, the bot uses `https://t.me/<BOT_USERNAME>?startgroup=true`.

### Admin permission note

Group admin means the actual Telegram admins of each group. The bot checks the current group with Telegram `get_chat_member()`. You do not need to put group admins in `.env`.

Global owner-only commands such as `/admin`, `/addadder`, `/rmadder`, `/give`, `/transfer`, and `/clmute` are restricted to `OWNER_ID`.
