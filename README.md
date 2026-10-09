# BIKA Character Catcher Bot — Python Async Version

A production-ready Telegram character catcher bot built with `python-telegram-bot` and MongoDB with PyMongo Async.

## Main features

- Group message counter based random character drops

- Scheduled rarity spawn system:
  - Normal drops: random Common / Uncommon / Rare
  - Every 20 drops: Legendary
  - Every 100 drops: Mystical
  - Every 300 drops: Divine
  - Every 400 drops: CrossVerse
  - Every 500 drops: Cataphract 70% / Supreme 30%
- Per-group `/changetime`
  - Group admins: 100 to 999
  - Owner: 1 to 3000
- Default changetime: 100
- `/bika <name>` first correct claimer wins the spawned card
- `/harem` and `.harem` card list with pagination buttons
- `/fav <id>` and `.fav <id>` favourite card support
- `/profile`, `/check <id>`
- `.gift <id> [qty]` or `/gift <id> [qty]` with confirm/cancel buttons
- Owner/adders `/add` media support in configured adding groups with Bika Database private channel archive
- No approve system: bot works immediately after being added to a group
- Sends a log to `GROUP_LOG_CHANNEL_ID` when bot is added to a new group
- Anti-spam: if one user sends 6 messages in a row, bot ignores that user for 10 minutes in that group

- Owner `/clmute` to clear bot-internal mutes
- Owner `/transfer oldid newid` or `/transfer oldid` + reply user to move a full harem
- Owner `/addadder` and `/rmadder` to allow/remove non-owner card adders in the configured Adding Group
- Owner `/give cardid` + reply user to add one card directly
- `/topgroup` top 10 groups by `/bika` catch count
- `/gtop` global top 10 users by total harem character count
- `/todaygtop` Myanmar/Yangon daily top 10 users by `/bika` catch count
- Each user can catch only 25 cards per Myanmar/Yangon day by default
- `/mylimit` checks used and remaining daily catch slots

## Rarity custom emoji configuration

All 10 rarities use one centralized environment-driven custom emoji system. Each rarity has its own `RARITY_<NAME>_CUSTOM_EMOJI_ID`, so changing one rarity does not affect the others.

```env
RARITY_LIMITED_CUSTOM_EMOJI_ID=
RARITY_COMMON_CUSTOM_EMOJI_ID=
RARITY_UNCOMMON_CUSTOM_EMOJI_ID=
RARITY_RARE_CUSTOM_EMOJI_ID=
RARITY_LEGENDARY_CUSTOM_EMOJI_ID=
RARITY_MYSTICAL_CUSTOM_EMOJI_ID=
RARITY_DIVINE_CUSTOM_EMOJI_ID=
RARITY_CROSSVERSE_CUSTOM_EMOJI_ID=
RARITY_CATAPHRACT_CUSTOM_EMOJI_ID=
RARITY_SUPREME_CUSTOM_EMOJI_ID=
```

When a custom ID is configured, the central rarity formatter emits Telegram `<tg-emoji>` markup for HTML/Rich Message surfaces, and rarity buttons send the same ID through Telegram's button custom-emoji field. This keeps rarity output consistent across drops, claims, checks, favourites, gifts, inline results, harem, profile tables, admin views, and rarity-selection buttons.

Fallback variables (`RARITY_<NAME>_FALLBACK_EMOJI`) are used only when a custom ID is empty or a surface cannot carry the custom icon. Existing `LIMITED_CUSTOM_EMOJI_ID` / `LIMITED_FALLBACK_EMOJI` deployments remain compatible.

## Important Telegram note

Telegram custom premium emoji are configured through the rarity `*_CUSTOM_EMOJI_ID` environment variables. HTML/Rich Message outputs use Telegram custom-emoji entities, while rarity buttons use the same custom emoji ID through the button API field. Unicode fallbacks remain available for unsupported/empty configurations.

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

For the complete Burmese adding guide, use <code>/addhelp</code> in Telegram.

Fast adding helpers:

```text
/addmode Anime | Rarity
/addanime Anime Name
# အသစ်ဆို MongoDB animes catalog ထဲကို သိမ်းမယ်
/addhelp
```


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

## Rarity spawn schedule

Each group has its own `totalDrops` counter. When that group reaches its changetime and a card spawns, `totalDrops` increases by 1 and the bot chooses rarity by this priority:

```text
Every 500 drops: Cataphract 70% / Supreme 30%
Every 400 drops: CrossVerse
Every 300 drops: Divine
Every 100 drops: Mystical
Every 20 drops : Legendary
Other drops    : random Common / Uncommon / Rare
```

Higher rules win if a number matches more than one rule. For example, drop 100 is Mystical, drop 300 is Divine, drop 500 is Cataphract/Supreme. If the selected rarity has no cards in the database yet, the bot safely falls back to another available card so the spawn does not fail.

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

Allow or remove extra users who can add cards in the configured Adding Group:

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


### High Rarity Captcha
Divine, CrossVerse, Cataphract, and Supreme drops require captcha. The first correct `/bika name` user must solve one correct option among five within `CLAIM_CAPTCHA_SECONDS` seconds. Wrong answer or timeout loses that drop.


## High-rarity pre-spawn captcha

For Divine, CrossVerse, Cataphract and Supreme scheduled drops, the bot now sends a captcha before showing the character media.

- Correct button within `CLAIM_CAPTCHA_SECONDS` => the character spawns normally.
- Wrong button => that scheduled drop is lost.
- Timeout => that scheduled drop is lost.
- After the character spawns, users catch it normally with `/bika name`.


## Render Web Service Webhook Deploy

This version runs in webhook mode by default. Use these Render settings:

```bash
Build Command: pip install -r requirements.txt
Start Command: python bot.py
```

Required webhook env values:

```env
RUN_MODE=webhook
WEBHOOK_URL=https://your-render-service.onrender.com
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET_TOKEN=change-this-random-secret
WEBHOOK_DROP_PENDING_UPDATES=false
PORT=10000
```

`WEBHOOK_URL` must be your public Render URL without a trailing slash. The bot will set Telegram webhook to `WEBHOOK_URL + WEBHOOK_PATH`.

For local testing without webhook, set:

```env
RUN_MODE=polling
```

UptimeRobot can ping `/` every 5 minutes. The health routes are `/` and `/health`.

## New Card Adding Controls

The configured Adding Group is the only place where `/add` media can be added. DM / Private Chat adding is disabled.

### `/addmode`

`/addmode` opens a compact control with three buttons:

- `Rarity` — primary
- `Anime Search` — success, Telegram inline Anime search
- `Close` — danger

Anime Search uses the database Anime catalog. For example, type `G` to show Anime names starting with G. The inline result displays the stored canonical Anime name exactly as saved in the database, with `[🎮]` only when that marker is already part of the stored name.

The shortcut remains supported:

`/addmode Genshin Impact | Lg`

When an Anime default exists but Rarity is not set, `/add Name` pauses with a Rarity prompt. The adder can choose a button or send `Un`, `Co`, `Ra`, `Lg`, `My`, `Dv`, `Cv`, `Ca`, or `Su`; the prompt/input is removed and the Card is saved.

### `/addanime`

`/addanime` shows Anime entries known by the database, with pagination and `Back`, `Next`, `Add New`, and `Close` controls.

`Add New` opens the same style of Telegram inline Anime search. Selecting an existing Anime changes the current add-mode default; choosing the add-new result writes the Anime to the MongoDB `animes` catalog and sets it as the default.

### `/update ID`

Use `/update 25` in the Adding Group to select an existing card. The bot shows that card's current fields. Upload the replacement media with a caption such as `/update Acheron | Lg | Honkai Star Rail`. Name, Rarity, and Anime are required. The explicit update replaces the existing card's Name, Rarity, Anime, and media for that exact ID; it does not create a new ID. Duplicate checks against other cards are intentionally skipped during `/update`. The update is refused if the target disappears or the archive update fails. The update session expires after 10 minutes. The normal `/add` flow remains separate and its duplicate checks are unchanged.
