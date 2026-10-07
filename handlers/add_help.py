from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


ADD_HELP_TEXT = """<b>🎴 BIKA CARD ADDING GUIDE</b>

ဒီ Bot ရဲ့ Card Adding စနစ်ကို အောက်ပါအတိုင်း အသုံးပြုနိုင်ပါတယ်။

<b>1️⃣ Card ထည့်ရန် အခြေခံပုံစံ</b>

Media (Photo / Video / GIF / Image-Video Document) ကို သတ်မှတ်ထားတဲ့ Adding Group ထဲမှာ တိုက်ရိုက် upload လုပ်ပြီး caption <code>/add</code> နဲ့ ထည့်နိုင်ပါတယ်။

<b>အမြန်ဆုံး</b>
<code>/add Yelan</code>
→ /addmode မှာ သတ်မှတ်ထားတဲ့ Anime + Rarity ကို အသုံးပြုမယ်။

<b>Rarity ပဲပြောင်း</b>
<code>/add Yelan | Lg</code>
→ Anime ကို /addmode default ကနေယူမယ်။

<b>အပြည့်အစုံ</b>
<code>/add Yelan | Lg | Genshin Impact</code>

<b>သတ်မှတ်ထားတဲ့ ID နဲ့</b>
<code>/add 123 | Yelan | Lg | Genshin Impact</code>
→ ID 123 ရှိပြီးသားဆို Update လုပ်မယ်။ မရှိရင် အဲဒီ ID နဲ့ Save လုပ်မယ်။

<b>2️⃣ Rarity အတိုကောက်</b>

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

<b>3️⃣ /addmode — အမြန် Card Adding</b>

<code>/addmode Anime | Rarity</code>
ဥပမာ — <code>/addmode Genshin Impact | Lg</code>

ဒီလိုသတ်မှတ်ပြီးရင်
<code>/add Yelan</code>
လို့ပဲ Media caption နဲ့ ထည့်နိုင်ပါတယ်။

<code>/addmode</code>
→ လက်ရှိ Anime / Rarity ကိုကြည့်ပြီး Button နဲ့ ရွေးနိုင်ပါတယ်။

<code>/addmode clear</code>
→ Default Anime + Rarity ကိုဖျက်မယ်။

<b>4️⃣ /addanime — Anime ကို သီးသန့်သတ်မှတ်ရန်</b>

<code>/addanime</code>
→ Database ထဲမှာ အသုံးများတဲ့ Anime စာရင်းကနေ ရွေးနိုင်ပါတယ်။

<code>/addanime Genshin Impact</code>
→ Anime အသစ်ဖြစ်ရင် MongoDB ရဲ့ <code>animes</code> catalog ထဲကို အလိုအလျောက်သိမ်းမယ်။ ရှိပြီးသားဆို duplicate မဖန်တီးဘဲ canonical Anime ကို ပြန်သုံးမယ်။ လက်ရှိ Rarity ကို မပြောင်းပါ။

Anime name ကို space/စာလုံးအကြီးအသေး normalize လုပ်ပြီး MongoDB <code>animes</code> catalog ထဲမှာ သိမ်းပါတယ်။ ရှိပြီးသား Anime ဆို canonical spelling ကို ပြန်သုံးပြီး duplicate catalog entry မဖန်တီးပါ။

<b>5️⃣ Duplicate ကာကွယ်မှု</b>

တူညီတဲ့ Telegram <code>file_unique_id</code> သို့မဟုတ် တူညီတဲ့ Name + Anime တွေ့ရင် ချက်ချင်း overwrite မလုပ်ပါ။

Button ၃ ခုနဲ့ ဆုံးဖြတ်နိုင်ပါတယ် —
• <b>Update Existing</b> → ရှိပြီးသား Card ကို update
• <b>Create New</b> → ID အသစ်နဲ့ Card အသစ်ဖန်တီး
• <b>Cancel</b> → မထည့်တော့ဘူး

<b>6️⃣ ID စနစ်</b>

Normal Card မှာ ID မထည့်ရင် System က Atomic Counter နဲ့ ID အသစ်ကို အလိုအလျောက်ပေးပါတယ်။ Concurrent add ဖြစ်ရင် ID ထပ်မတူအောင် ထိန်းထားပါတယ်။

Limited Card ရဲ့ Custom ID ဥပမာ <code>1a</code> ကို သီးခြား collection မှာ သိမ်းပြီး Owner သာ Add/Update လုပ်နိုင်ပါတယ်။

<b>7️⃣ Media</b>

Photo, Video, Animation/GIF နဲ့ Image/Video Document ကို Add လုပ်နိုင်ပါတယ်။
Forwarded media နဲ့ /add ကို လက်ခံမထားပါ။

<b>8️⃣ Data Safety</b>

Card ကို အရင် Bika Database archive channel ထဲသိမ်းပြီးမှ MongoDB ကို authoritative data အဖြစ် Save လုပ်ပါတယ်။ SQLite hot lookup က performance အတွက်သာဖြစ်ပြီး MongoDB data ကို မဖျက်ပါ။

<b>9️⃣ Add လုပ်ရာမှာ မဖြစ်မနေသတိထားရန်</b>

• /add သုံးသူက Owner သို့မဟုတ် ခွင့်ပြုထားတဲ့ Adder ဖြစ်ရမယ်။
• Media ကို သတ်မှတ်ထားတဲ့ Adding Group ထဲကနေ တိုက်ရိုက် upload လုပ်ရမယ်။
• Rarity + Anime မပြည့်စုံရင် /addmode သတ်မှတ်ပါ သို့မဟုတ် /add မှာ တိုက်ရိုက်ထည့်ပါ။
• Duplicate တွေ့ရင် Button ကို သေချာရွေးပါ။
• Limited Card က Owner-only ဖြစ်ပြီး Custom ID လိုပါတယ်။

<b>🔟 အမြန်အသုံးပြုမှု</b>

<code>/addmode Genshin Impact | Lg</code>
ပြီးရင် Media caption:
<code>/add Yelan</code>

Anime ပြောင်းချင်ရင်:
<code>/addanime Honkai Star Rail</code>
ပြီးရင် နောက် Card တွေကို <code>/add Name</code> နဲ့ ဆက်ထည့်နိုင်ပါတယ်။

အကူအညီလိုရင် <code>/addhelp</code> ကို ထပ်ခေါ်နိုင်ပါတယ်။
"""


async def addhelp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(ADD_HELP_TEXT, parse_mode="HTML")


def register_add_help_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("addhelp", addhelp_cmd))
