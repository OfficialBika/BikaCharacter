from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


ADD_HELP_TEXT = """<b>🎴 BIKA CARD ADDING GUIDE</b>

<b>📍 Where to add</b>

Card Adding ကို <b>သတ်မှတ်ထားတဲ့ Adding Group</b> ထဲမှာပဲ လုပ်ရပါတယ်။
DM / Private Chat ကနေ <code>/add</code> နဲ့ Card Add မလုပ်နိုင်ပါ။
Adder က Owner သို့မဟုတ် Owner က <code>/addadder</code> နဲ့ ခွင့်ပြုထားသူ ဖြစ်ရပါမယ်။

<b>1️⃣ /addmode — Fast Card Adding</b>

<code>/addmode</code> ကိုပို့လိုက်ရင် ဒီလို panel ပေါ်ပါမယ် —

<code>⚙️ CARD ADD MODE

သင်ထည့်သွင်းလိုသော Anime ကိုရွေးပါ။

Anime: ...
Rarity: ...

Set a default Anime + Rarity, then add many cards quickly.
You can also use: /addmode Anime | Lg</code>

အောက်မှာ Button ၃ ခုပါမယ် —
<b>Rarity</b> = Primary
<b>Anime Search</b> = Success
<b>Close</b> = Danger

<b>Rarity ရွေးရန်</b>
Rarity ကိုနှိပ်ပြီး Un / Co / Ra / Lg / My / Dv / Cv / Ca / Su ထဲက ရွေးနိုင်ပါတယ်။
ရွေးပြီးရင် /addmode panel ထဲက Rarity ကို ချက်ချင်း update လုပ်ပေးပါတယ်။

<b>Anime ရွေးရန်</b>
<b>Anime Search</b> ကိုနှိပ်ပြီး Inline Search ကိုဖွင့်ပါ။
ဥပမာ <code>G</code> လို့ရိုက်ရင် G နဲ့စတဲ့ Anime တွေကို ပြပါမယ်။
ဥပမာ <code>Genshin Impact [🎮]</code> ကိုရွေးလိုက်ရင် Inline result message ကိုဖျက်ပြီး /addmode panel ထဲမှာ Anime ကို သတ်မှတ်ပေးပါမယ်။

Shortcut အနေနဲ့ —
<code>/addmode Genshin Impact | Lg</code>

<b>2️⃣ Default သတ်မှတ်ပြီး Card အမြန်ထည့်ရန်</b>

Anime + Rarity နှစ်ခုလုံး သတ်မှတ်ပြီးရင် Media caption ကို —

<code>/add Yelan</code>

လို့ပဲ ပို့နိုင်ပါတယ်။
Bot က /addmode default Anime + Rarity ကို အလိုအလျောက် အသုံးပြုပြီး Card Save လုပ်ပေးပါမယ်။

Rarity ကို Card တစ်ခုချင်းပြောင်းချင်ရင် —
<code>/add Yelan | Lg</code>

Anime ကိုလည်း တိုက်ရိုက်ပေးနိုင်ပါတယ် —
<code>/add Yelan | Lg | Genshin Impact</code>

<b>3️⃣ Rarity မရွေးထားရင်</b>

/addmode မှာ Anime ရှိပေမယ့် Rarity မရှိသေးရင် Media + Name ကို ပို့လိုက်တဲ့အခါ Bot က —

<b>🎴 RARITY REQUIRED</b>

လို့ပြပြီး ဒီ Card အတွက် Rarity သတ်မှတ်ခိုင်းပါမယ်။

Button နဲ့ ရွေးနိုင်သလို အောက်က code တွေထဲက တစ်ခုကို တိုက်ရိုက်ပို့လည်းရပါတယ် —

<code>Un Co Ra Lg My Dv Cv Ca Su</code>

ဥပမာ —
<code>Lg</code>

ရွေးလိုက်တာနဲ့ Rarity prompt ကိုဖျက်ပြီး Card ကို Save လုပ်ကာ <b>✅ Card Saved</b> အချက်အလက်ကို ပြပါမယ်။

<b>4️⃣ Rarity အတိုကောက်</b>

<code>Su</code> = Supreme
<code>Cv</code> = CrossVerse
<code>Ca</code> = Cataphract
<code>Dv</code> = Divine
<code>My</code> = Mystical
<code>Lg</code> = Legendary
<code>Ra</code> = Rare
<code>Un</code> = Uncommon
<code>Co</code> = Common

Full rarity name တွေကိုလည်း ဆက်သုံးနိုင်ပါတယ်။

<b>5️⃣ /addanime — Anime Database Selector</b>

<code>/addanime</code> ကိုပို့လိုက်ရင် Database ထဲမှာရှိတဲ့ Anime list ကို Page ခွဲပြီးပြပါမယ်။

အောက်မှာ —
<b>Back</b> = အရင် Page
<b>Next</b> = နောက် Page
<b>Add New</b> = Inline Search ကနေ Anime အသစ်ရှာ/ထည့်
<b>Close</b> = Selector ပိတ်

Database ထဲက Anime တစ်ခုကို ရွေးလိုက်ရင် လက်ရှိ Rarity ကို မပြောင်းဘဲ အဲဒီ Anime ကို default Anime အဖြစ် သတ်မှတ်ပြီး Card Add mode panel ကို ပြန်ပြပါမယ်။

<b>Add New</b> ကနေ Search လုပ်ပြီးသား Anime ရှိရင် အဲဒီ Anime ကိုရွေးနိုင်ပါတယ်။ မရှိသေးရင် <b>➕ Add</b> result ကနေ MongoDB <code>animes</code> catalog ထဲကို အသစ်ထည့်ပြီး default Anime အဖြစ် သတ်မှတ်ပါမယ်။

အဟောင်း shortcut အနေနဲ့ —
<code>/addanime Genshin Impact</code>

လည်း ဆက်သုံးနိုင်ပါတယ်။

<b>6️⃣ Normal / Limited Card Add</b>

Normal Card —
<code>/add 123 | Yelan | Lg | Genshin Impact</code>
မရှိသေးရင် ID 123 နဲ့ Save လုပ်ပါမယ်။
ရှိပြီးသားဆို Update flow ကို အသုံးပြုပါမယ်။

Limited Card —
<code>/add 1a | Special | Limited | Bika Limited</code>

Limited Card က <b>Owner-only</b> ဖြစ်ပြီး Custom ID လိုပါတယ်။

<b>7️⃣ Duplicate ကာကွယ်မှု</b>

တူညီတဲ့ media သို့မဟုတ် Name + Anime duplicate ဖြစ်နိုင်ရင် Bot က ချက်ချင်း overwrite မလုပ်ပါ။

Button ၃ ခုနဲ့ ရွေးနိုင်ပါတယ် —
<b>♻️ Update Existing</b>
<b>➕ Create New</b>
<b>✕ Cancel</b>

<b>8️⃣ Media rules</b>

Photo, Video, Animation/GIF နဲ့ Image/Video Document ကို Add လုပ်နိုင်ပါတယ်။
Forwarded media + <code>/add</code> ကို လက်မခံပါ။
Media ကို Adding Group ထဲမှာ တိုက်ရိုက် upload လုပ်ရပါမယ်။

<b>9️⃣ Data safety</b>

Card media ကို Bika Database archive channel ထဲမှာ သိမ်းပြီး MongoDB ကို authoritative card record အဖြစ် Save လုပ်ပါတယ်။
Archive edit / MongoDB save အတွင်း failure ဖြစ်ရင် orphan archive မကျန်အောင် cleanup / rollback ကို ကြိုးစားပေးထားပါတယ်။
SQLite hot lookup က performance cache အဖြစ်သာ အသုံးပြုပါတယ်။

<b>🔟 အမြန်အသုံးပြုနိုင်တဲ့ Flow</b>

<code>/addmode</code>
→ Rarity ရွေး
→ Anime Search
→ ဥပမာ Genshin Impact [🎮] ရွေး
→

<code>/add Yelan</code>
<code>/add Hu Tao</code>
<code>/add Keqing</code>

လိုပဲ Card အများကြီးကို default Anime + Rarity နဲ့ အမြန်ထည့်နိုင်ပါတယ်။

Rarity default မရှိရင် —
Media + <code>/add Name</code>
→ Bot က Rarity တောင်း
→ <code>Lg</code> လို code ပို့
→ Prompt ဖျက်
→ Card Saved

အကူအညီလိုရင် <code>/addhelp</code> ကို ထပ်ခေါ်နိုင်ပါတယ်။
"""


async def addhelp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(ADD_HELP_TEXT, parse_mode="HTML")


def register_add_help_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("addhelp", addhelp_cmd))
