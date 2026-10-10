import asyncio
import logging
import sys
import re
import os
import shutil
import tempfile
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, BotCommandScopeDefault, LabeledPrice, PreCheckoutQuery, Message

from config import BOT_TOKEN, OWNER_ID, REQUIRED_CHANNELS
import database as db
from userbot import start_userbot_for_account, active_clients, extract_account_info, get_devices, terminate_device, process_uploaded_session, terminate_bot_session

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

CO_OWNER_ID = 0

STAR_RATE = 1.3
DEPOSIT_MIN_AMOUNT = 30
UPI_ID = "yourupi@okbank"          # TODO: replace with your real UPI ID
UPI_QR_IMAGE_PATH = "upi_qr.jpg"   # TODO: put your QR code image file at this path (same folder as bot.py)
GP_MIN_AMOUNT = 30
GP_MAX_AMOUNT = 10000
GP_RATE = 1.0        # wallet balance given per Rs.1 of redeem code
MIN_STARS = 15
MAX_STARS = 10000   # Telegram invoice limit


PRIVACY_POLICY_TEXT = """📜 Privacy Policy & Terms – Tg Otp Store Bot

Last updated: October 2026

1. Data We Collect
When you use this bot, we store:
- Your Telegram user ID
- Your wallet balance
- Your payment and purchase history
- Promo codes you have redeemed and referral information

We do not collect your phone number, real name, or any card or payment details. Payments made with Telegram Stars are processed entirely by Telegram.

2. How We Use Your Data
- To manage your wallet and process top-ups
- To deliver the accounts you purchase
- To prevent fraud, abuse, and duplicate payments
- To provide customer support

3. Data Sharing
We do not sell or share your data with third parties. Data may be disclosed only if required by law.

4. Data Security
Your data is stored in a secured database with restricted access. No system is 100% secure, but we take reasonable steps to protect it.

5. No Refund & No Liability Policy
- All purchases are final. Once an account is delivered, no refund will be given.
- We are only responsible for delivering the account details and the login OTP.
- After the account is logged in and the OTP is delivered, we are not responsible for anything that happens to the account, including but not limited to freeze, ban, restriction, logout, session termination, or loss of access.
- Account safety after delivery depends on how you use it. Any issue after delivery is entirely at your own risk.
- Wallet balance and Stars top-ups cannot be withdrawn or refunded.

6. Acceptance of Terms
By using this bot and making a purchase, you confirm that you have read and agreed to these terms.

7. Your Choices
You can stop using the bot at any time. To request deletion of your data, contact us.

8. Contact
Telegram: @izoph"""

_SMALL_CAPS_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ', 'h': 'ʜ', 'i': 'ɪ',
    'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ', 'o': 'ᴏ', 'p': 'ᴘ', 'q': 'q', 'r': 'ʀ',
    's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ', 'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
}

def sc(text: str) -> str:
    """Converts text to small-caps style (used for button labels and headings)."""
    return "".join(_SMALL_CAPS_MAP.get(ch.lower(), ch) for ch in text)

def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id == CO_OWNER_ID


# ---------------- Ban middleware (applies to all handlers) ----------------
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and not is_admin(user.id):
            u = await db.get_user(user.id)
            if u and u.get("is_banned", 0) == 1:
                if isinstance(event, types.CallbackQuery):
                    await event.answer("❌ You are banned from using this bot.", show_alert=True)
                elif isinstance(event, types.Message):
                    await event.answer("❌ You are banned from using this bot.")
                return
        return await handler(event, data)


dp.message.middleware(BanMiddleware())
dp.callback_query.middleware(BanMiddleware())


class AddItemState(StatesGroup):
    waiting_for_display_name = State()
    waiting_for_price = State()
    waiting_for_session = State()
    waiting_for_two_step = State()

class AddCategoryState(StatesGroup):
    waiting_for_name = State()

class EditCategoryState(StatesGroup):
    waiting_for_new_name = State()

class BroadcastState(StatesGroup):
    waiting_for_message = State()

class BanUserState(StatesGroup):
    waiting_for_user_id = State()

class PromoCodeState(StatesGroup):
    waiting_for_code = State()

class SupplierConfigState(StatesGroup):
    waiting_for_url = State()
    waiting_for_key = State()

class GPTopupState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_code = State()
    waiting_for_screenshot = State()

class DepositState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_screenshot = State()

class StarsTopupState(StatesGroup):
    waiting_for_amount = State()

class CreatePromoState(StatesGroup):
    waiting_for_code_name = State()
    waiting_for_code_amount = State()


def mask_phone_number(phone: str) -> str:
    if len(phone) > 8:
        return phone[:3] + "******" + phone[-2:]
    return phone

async def set_bot_commands(bot_instance: Bot):
    user_commands = [
        BotCommand(command="start", description="Start the bot 🚀"),
        BotCommand(command="wallet", description="Open your wallet 💰"),
        BotCommand(command="myorders", description="View your purchase history 📦"),
        BotCommand(command="promo", description="Redeem promo code 🎁"),
        BotCommand(command="referral", description="Invite & earn bonus 👥"),
        BotCommand(command="deposit", description="Deposit funds (UPI/Crypto) 💳"),
        BotCommand(command="privacy", description="Privacy Policy & Terms 📜"),
    ]
    await bot_instance.set_my_commands(user_commands, scope=BotCommandScopeDefault())

    owner_commands = [
        BotCommand(command="start", description="Start the bot 🚀"),
        BotCommand(command="wallet", description="Open your wallet 💰"),
        BotCommand(command="myorders", description="View your purchase history 📦"),
        BotCommand(command="promo", description="Redeem promo code 🎁"),
        BotCommand(command="referral", description="Invite & earn bonus 👥"),
        BotCommand(command="deposit", description="Deposit funds (UPI/Crypto) 💳"),
        BotCommand(command="privacy", description="Privacy Policy & Terms 📜"),
        BotCommand(command="admin", description="Open Admin Panel ⚙"),
        BotCommand(command="add", description="Add new account ➕"),
        BotCommand(command="dd", description="Add balance to a user 💰"),
        BotCommand(command="ss", description="Deduct balance from a user 💸"),
    ]
    try:
        await bot_instance.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))
        if CO_OWNER_ID:
            await bot_instance.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=CO_OWNER_ID))
    except Exception as e:
        print(f"Command setup error: {e}")

async def check_user_channels(user_id: int) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True

async def send_main_menu(message_or_callback, text="🛒 Welcome to the bot! Explore the menu below to buy accounts and manage your wallet."):
    user_id = message_or_callback.from_user.id

    user_data = await db.get_user(user_id)
    if user_data and user_data.get('is_banned', 0) == 1:
        if isinstance(message_or_callback, types.CallbackQuery):
            await message_or_callback.answer("❌ You are banned from using this bot.", show_alert=True)
        else:
            await message_or_callback.answer("❌ You are banned from using this bot.")
        return

    kb = [
        [
            InlineKeyboardButton(text=sc("🛒 Buy Now"), callback_data="shop"),
            InlineKeyboardButton(text=sc("💳 Wallet"), callback_data="wallet")
        ],
        [
            InlineKeyboardButton(text=sc("📦 My Orders"), callback_data="my_orders"),
            InlineKeyboardButton(text=sc("🎁 Redeem Promo"), callback_data="redeem_promo_menu")
        ],
        [
            InlineKeyboardButton(text=sc("👥 Referral"), callback_data="referral_menu"),
            InlineKeyboardButton(text=sc("👤 Profile"), callback_data="my_profile")
        ],
        [
            InlineKeyboardButton(text=sc("🆘 Support"), callback_data="support"),
            InlineKeyboardButton(text=sc("📜 Privacy Policy"), callback_data="privacy_policy")
        ]
    ]
    if is_admin(user_id):
        kb.append([InlineKeyboardButton(text=sc("⚙️ Admin Panel"), callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await message_or_callback.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    args = message.text.split()
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referrer_id = int(args[1].split("_")[1])
            if referrer_id != user_id:
                user_record = await db.get_user(user_id)
                if user_record.get("referred_by") is None:
                    await db.users_col.update_one(
                        {"user_id": user_id},
                        {"$set": {"referred_by": referrer_id, "referral_rewarded": 0}}
                    )
        except Exception:
            pass

    user_data = await db.get_user(user_id)
    if user_data and user_data.get('is_banned', 0) == 1:
        await message.answer("❌ You are banned from using this bot.")
        return

    if user_data["is_verified"] == 1:
        still_member = await check_user_channels(user_id)
        if still_member:
            await send_main_menu(message, f"Welcome back, {message.from_user.first_name}!\n\n🛒 Explore the menu below to buy accounts.")
            return
        else:
            await db.update_verification(user_id, 0)

    kb = []
    for idx, ch in enumerate(REQUIRED_CHANNELS, start=1):
        kb.append([InlineKeyboardButton(text=sc(f"📢 Join Channel {idx}"), url=f"https://t.me/{ch.lstrip('@')}")])
    kb.append([InlineKeyboardButton(text=sc("✅ Verify Membership"), callback_data="verify_membership")])

    await message.answer(
        "👋 **ᴡᴇʟᴄᴏᴍᴇ!**\n\nTo use this bot, you must join our required channels first. Please join them and click **'Verify Membership'**:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "verify_membership")
async def verify_membership_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    joined = await check_user_channels(user_id)

    if joined:
        await db.update_verification(user_id, 1)

        user_data = await db.get_user(user_id)
        if user_data.get("referred_by") and user_data.get("referral_rewarded", 0) == 0:
            referrer_id = user_data["referred_by"]
            # atomic: reward is given only once
            res = await db.users_col.update_one(
                {"user_id": user_id, "referral_rewarded": 0},
                {"$set": {"referral_rewarded": 1}}
            )
            if res.modified_count == 1:
                await db.users_col.update_one(
                    {"user_id": referrer_id},
                    {"$inc": {"referral_count": 1, "balance": 0.001}}
                )
                try:
                    await bot.send_message(
                        referrer_id,
                        f"🎉 **ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜꜱ!** User `{user_id}` verified their membership using your link. **₹0.001** added to your balance!",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        await callback.answer("Verified successfully! ✅", show_alert=True)
        await send_main_menu(callback, "🎉 **ᴠᴇʀɪꜰɪᴄᴀᴛɪᴏɴ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ!**\n\n🛒 Explore the menu below.")
    else:
        await callback.answer("❌ You haven't joined all required channels yet! Please join them first.", show_alert=True)

@dp.message(Command("wallet"))
@dp.callback_query(F.data == "wallet")
async def show_wallet(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    user = await db.get_user(user_id)
    payments = await db.get_user_payments(user_id)

    history_text = "📜 **ʀᴇᴄᴇɴᴛ ᴘᴀʏᴍᴇɴᴛ ʜɪꜱᴛᴏʀʏ:**\n"
    if not payments:
        history_text += "No payment history yet."
    else:
        for p in payments[:5]:
            history_text += f"• ₹{p['amount']} | Status: `{p['status']}`\n"

    kb = [
        [InlineKeyboardButton(text=sc("➕ Add Funds"), callback_data="add_funds_menu")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]
    ]

    text = (
        f"💰 **ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ & ʜɪꜱᴛᴏʀʏ**\n\n"
        f"💵 Balance: **₹{user['balance']:.3f}**\n\n"
        f"{history_text}"
    )

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "add_funds_menu")
async def add_funds_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    kb = [
        [InlineKeyboardButton(text=sc("⭐ Telegram Stars"), callback_data="add_funds_stars")],
        [InlineKeyboardButton(text=sc("🎁 Google Play Code"), callback_data="add_funds_gp")],
        [InlineKeyboardButton(text=sc("💳 Deposit (UPI / Crypto)"), callback_data="add_funds_deposit")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data="wallet")]
    ]
    await callback.message.edit_text(
        "➕ **ᴀᴅᴅ ꜰᴜɴᴅꜱ**\n\nChoose a payment method:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "add_funds_stars")
async def add_funds_stars_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(StarsTopupState.waiting_for_amount)
    await callback.message.edit_text(
        "⭐ **ᴀᴅᴅ ꜰᴜɴᴅꜱ ᴠɪᴀ ᴛᴇʟᴇɢʀᴀᴍ ꜱᴛᴀʀꜱ**\n\n"
        f"• Minimum top-up: **{MIN_STARS} Stars**\n"
        f"• Conversion rate: `1 Star = ₹{STAR_RATE}`\n\n"
        "✍️ How many Stars do you want to add? Send a number (e.g., `50`):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="cancel_topup")]]),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(StarsTopupState.waiting_for_amount)
async def send_stars_invoice_action(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Please send a whole number only (e.g., 50).")
        return

    stars_count = int(text)
    if stars_count < MIN_STARS:
        await message.answer(f"❌ Minimum top-up is {MIN_STARS} Stars. Please send a higher number:")
        return
    if stars_count > MAX_STARS:
        await message.answer(f"❌ Maximum is {MAX_STARS} Stars per top-up. Please send a lower number:")
        return

    await state.clear()
    inr_value = stars_count * STAR_RATE
    prices = [LabeledPrice(label=f"{stars_count} Telegram Stars", amount=stars_count)]

    await bot.send_invoice(
        chat_id=message.from_user.id,
        title="Add Wallet Balance",
        description=f"Add ₹{inr_value:.2f} to your bot balance using Telegram Stars.",
        payload=f"topup_{stars_count}",
        currency="XTR",
        prices=prices,
        provider_token=""
    )

@dp.pre_checkout_query()
async def process_stars_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def process_stars_successful_payment(message: Message):
    pay = message.successful_payment

    if not pay.invoice_payload.startswith("topup_"):
        return

    user_id = message.from_user.id
    stars_paid = pay.total_amount            # amount verified by Telegram
    inr_added = stars_paid * STAR_RATE

    # duplicate protection: the same charge_id is never processed twice
    is_new = await db.save_star_payment(
        pay.telegram_payment_charge_id, user_id, stars_paid, inr_added
    )
    if not is_new:
        return

    await db.update_balance(user_id, inr_added)

    await message.answer(
        f"✅ **ᴘᴀʏᴍᴇɴᴛ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ!**\n\n"
        f"Successfully paid **{stars_paid} Stars ⭐**.\n"
        f"Added **₹{inr_added:.2f}** to your wallet balance!",
        parse_mode="Markdown"
    )

# ======================= GOOGLE PLAY CODE TOPUP =======================

def gp_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="cancel_topup")]])

def gp_admin_caption(req: dict, status_line: str = "") -> str:
    text = (
        f"🎁 <b>Google Play Code Request #{req['request_id']}</b>\n\n"
        f"👤 User ID: <code>{req['user_id']}</code>\n"
        f"💰 Amount: ₹{req['amount']}\n"
        f"🔑 Code: <code>{req['code']}</code>"
    )
    if status_line:
        text += f"\n\n{status_line}"
    return text

@dp.callback_query(F.data == "cancel_topup")
async def cancel_topup(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await add_funds_menu(callback, state)

@dp.callback_query(F.data == "add_funds_gp")
async def add_funds_gp_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(GPTopupState.waiting_for_amount)
    await callback.message.edit_text(
        "🎁 **ᴀᴅᴅ ꜰᴜɴᴅꜱ ᴠɪᴀ ɢᴏᴏɢʟᴇ ᴘʟᴀʏ ᴄᴏᴅᴇ**\n\n"
        f"• Minimum amount: **₹{GP_MIN_AMOUNT}**\n"
        "• Code amount must end with **0 or 5** (e.g., 10, 15, 20, 25, 100)\n\n"
        "✍️ Enter the amount of your redeem code:",
        reply_markup=gp_cancel_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message(GPTopupState.waiting_for_amount)
async def gp_get_amount(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Please send a whole number only (e.g., 50).", reply_markup=gp_cancel_kb())
        return

    amount = int(text)
    if amount < GP_MIN_AMOUNT:
        await message.answer(f"❌ Minimum amount is ₹{GP_MIN_AMOUNT}. Please send a higher amount:", reply_markup=gp_cancel_kb())
        return
    if amount > GP_MAX_AMOUNT:
        await message.answer(f"❌ Maximum amount is ₹{GP_MAX_AMOUNT} per code. Please send a lower amount:", reply_markup=gp_cancel_kb())
        return
    if amount % 5 != 0:
        await message.answer(
            "❌ Invalid amount. The amount must end with 0 or 5 (e.g., 10, 15, 20, 25, 100). Please try again:",
            reply_markup=gp_cancel_kb()
        )
        return

    await state.update_data(gp_amount=amount)
    await state.set_state(GPTopupState.waiting_for_code)
    await message.answer(
        f"✅ Amount: **₹{amount}**\n\n"
        "🔑 Now send your **Google Play redeem code** (as text):",
        reply_markup=gp_cancel_kb(),
        parse_mode="Markdown"
    )

@dp.message(GPTopupState.waiting_for_code)
async def gp_get_code(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    code = re.sub(r"[\s-]", "", raw).upper()
    if not re.fullmatch(r"[A-Z0-9]{10,30}", code):
        await message.answer("❌ Invalid code format. Please send the redeem code as text:", reply_markup=gp_cancel_kb())
        return

    await state.update_data(gp_code=code)
    await state.set_state(GPTopupState.waiting_for_screenshot)
    await message.answer(
        "📸 Now send a **screenshot of the purchase** in which the redeem code is clearly visible.",
        reply_markup=gp_cancel_kb(),
        parse_mode="Markdown"
    )

@dp.message(GPTopupState.waiting_for_screenshot)
async def gp_get_screenshot(message: types.Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Please send the screenshot as a photo.", reply_markup=gp_cancel_kb())
        return

    data = await state.get_data()
    amount = data.get("gp_amount")
    code = data.get("gp_code")
    if not amount or not code:
        await state.clear()
        await message.answer("❌ Session expired. Please start again from the wallet.")
        return

    file_id = message.photo[-1].file_id
    req_id, result = await db.create_gp_request(message.from_user.id, amount, code, file_id)

    if result == "limit":
        await state.clear()
        await message.answer("❌ You already have pending requests. Please wait until they are reviewed.")
        return
    if result == "duplicate":
        await message.answer("❌ This redeem code has already been submitted.", reply_markup=gp_cancel_kb())
        return

    req = await db.get_gp_request(req_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=sc("✅ Accept"), callback_data=f"gp_ok_{req_id}"),
        InlineKeyboardButton(text=sc("❌ Reject"), callback_data=f"gp_no_{req_id}")
    ]])

    admin_ids = [OWNER_ID] + ([CO_OWNER_ID] if CO_OWNER_ID else [])
    delivered = False
    for admin_id in admin_ids:
        try:
            await bot.send_photo(admin_id, file_id, caption=gp_admin_caption(req), reply_markup=kb, parse_mode="HTML")
            delivered = True
        except Exception as e:
            print(f"GP admin notify error: {e}")

    await state.clear()
    if not delivered:
        await db.set_gp_status(req_id, "rejected")
        await db.release_gp_code(code)
        await message.answer("⚠️ Could not submit your request right now. Please try again later.")
        return

    await message.answer(
        f"✅ **ʀᴇqᴜᴇꜱᴛ ꜱᴜʙᴍɪᴛᴛᴇᴅ!**\n\n"
        f"💰 Amount: ₹{amount}\n"
        f"💳 You will receive: ₹{amount * GP_RATE:.2f}\n\n"
        "⏳ Your code will be verified soon. You will be notified once it is approved or rejected.",
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("gp_ok_"))
async def gp_approve(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    req_id = int(callback.data.split("_")[2])
    req = await db.set_gp_status(req_id, "approved")
    if not req:
        await callback.answer("Already processed.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    credit = req["amount"] * GP_RATE
    await db.update_balance(req["user_id"], credit)
    await db.save_payment_record(req["user_id"], credit, "GPLAY", "TOPUP_SUCCESS")

    try:
        await bot.send_message(
            req["user_id"],
            f"✅ **ɢᴏᴏɢʟᴇ ᴘʟᴀʏ ᴄᴏᴅᴇ ᴀᴘᴘʀᴏᴠᴇᴅ!**\n\nAdded **₹{credit:.2f}** to your wallet balance.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await callback.message.edit_caption(
            caption=gp_admin_caption(req, "✅ <b>APPROVED</b> (balance added)"),
            reply_markup=None, parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Approved ✅")

@dp.callback_query(F.data.startswith("gp_no_"))
async def gp_reject(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    req_id = int(callback.data.split("_")[2])
    req = await db.set_gp_status(req_id, "rejected")
    if not req:
        await callback.answer("Already processed.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    await db.release_gp_code(req["code"])

    try:
        await bot.send_message(
            req["user_id"],
            "❌ **ɢᴏᴏɢʟᴇ ᴘʟᴀʏ ᴄᴏᴅᴇ ʀᴇᴊᴇᴄᴛᴇᴅ.**\n\n"
            "Your redeem code could not be verified (invalid, already used, or not from a valid source). "
            "No balance was added. If you think this is a mistake, please contact support.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await callback.message.edit_caption(
            caption=gp_admin_caption(req, "❌ <b>REJECTED</b>"),
            reply_markup=None, parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Rejected ❌")

# ======================= DEPOSIT (UPI / CRYPTO) =======================

def deposit_admin_caption(req: dict, status_line: str = "") -> str:
    text = (
        f"💳 <b>Deposit Request #{req['request_id']}</b>\n\n"
        f"👤 User ID: <code>{req['user_id']}</code>\n"
        f"💰 Amount: ₹{req['amount']}\n"
        f"🏦 Method: {req['method']}"
    )
    if status_line:
        text += f"\n\n{status_line}"
    return text

@dp.message(Command("deposit"))
@dp.callback_query(F.data == "add_funds_deposit")
async def add_funds_deposit_menu(event: types.Message | types.CallbackQuery, state: FSMContext):
    await state.set_state(DepositState.waiting_for_amount)
    text = (
        "➕ **ᴅᴇᴘᴏꜱɪᴛ ꜰᴜɴᴅꜱ**\n\n"
        f"Minimum deposit: **₹{DEPOSIT_MIN_AMOUNT}**\n\n"
        "Please enter the amount you want to deposit (in ₹):"
    )
    markup = gp_cancel_kb()
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.message(DepositState.waiting_for_amount)
async def deposit_get_amount(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Please send a whole number only (e.g., 50).", reply_markup=gp_cancel_kb())
        return

    amount = int(text)
    if amount < DEPOSIT_MIN_AMOUNT:
        await message.answer(f"❌ Minimum deposit is ₹{DEPOSIT_MIN_AMOUNT}. Please send a higher amount:", reply_markup=gp_cancel_kb())
        return

    await state.update_data(dep_amount=amount)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=sc("₿ Crypto"), callback_data="dep_crypto")],
        [InlineKeyboardButton(text=sc("📱 UPI"), callback_data="dep_upi")],
        [InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="cancel_topup")]
    ])
    await message.answer(f"💰 Amount: ₹{amount}\n\nChoose a payment method:", reply_markup=kb)

@dp.callback_query(F.data == "dep_crypto")
async def deposit_pick_crypto(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "❌ This payment method is currently unavailable.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="add_funds_deposit")]])
    )
    await callback.answer()

@dp.callback_query(F.data == "dep_upi")
async def deposit_pick_upi(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("dep_amount")
    if not amount:
        await callback.answer("Session expired. Please start again.", show_alert=True)
        return

    await state.set_state(DepositState.waiting_for_screenshot)
    caption = (
        f"📱 **ᴜᴘɪ ᴘᴀʏᴍᴇɴᴛ**\n\n"
        f"💰 Amount: ₹{amount}\n"
        f"🆔 UPI ID: `{UPI_ID}`\n\n"
        f"Pay using the QR code and send the screenshot."
    )
    try:
        photo = types.FSInputFile(UPI_QR_IMAGE_PATH)
        await callback.message.answer_photo(photo, caption=caption, parse_mode="Markdown", reply_markup=gp_cancel_kb())
    except Exception:
        await callback.message.answer(caption, parse_mode="Markdown", reply_markup=gp_cancel_kb())
    await callback.answer()

@dp.message(DepositState.waiting_for_screenshot)
async def deposit_get_screenshot(message: types.Message, state: FSMContext):
    if not message.photo:
        await message.answer("❌ Please send the payment screenshot as a photo.", reply_markup=gp_cancel_kb())
        return

    data = await state.get_data()
    amount = data.get("dep_amount")
    if not amount:
        await state.clear()
        await message.answer("❌ Session expired. Please start again from the wallet.")
        return

    file_id = message.photo[-1].file_id
    await state.update_data(dep_file_id=file_id)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=sc("✅ Confirm"), callback_data="dep_confirm"),
        InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="dep_cancel_screenshot")
    ]])
    await message.answer_photo(
        file_id,
        caption=f"💰 Amount: ₹{amount}\n\nConfirm to submit the receipt?",
        reply_markup=kb
    )

@dp.callback_query(F.data == "dep_cancel_screenshot")
async def deposit_cancel_screenshot(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_caption(caption="❌ Deposit request cancelled.", reply_markup=None)
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data == "dep_confirm")
async def deposit_confirm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount = data.get("dep_amount")
    file_id = data.get("dep_file_id")
    if not amount or not file_id:
        await state.clear()
        await callback.answer("❌ Session expired.", show_alert=True)
        return

    req_id, result = await db.create_deposit_request(callback.from_user.id, amount, "UPI", file_id)
    await state.clear()

    if result == "pending":
        await callback.message.edit_caption(
            caption="❌ You already have a pending deposit request. Please wait until it is reviewed.",
            reply_markup=None
        )
        await callback.answer()
        return

    req = await db.get_deposit_request(req_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=sc("✅ Approve"), callback_data=f"dep_ok_{req_id}"),
        InlineKeyboardButton(text=sc("❌ Reject"), callback_data=f"dep_no_{req_id}")
    ]])

    admin_ids = [OWNER_ID] + ([CO_OWNER_ID] if CO_OWNER_ID else [])
    delivered = False
    for admin_id in admin_ids:
        try:
            await bot.send_photo(admin_id, file_id, caption=deposit_admin_caption(req), reply_markup=kb, parse_mode="HTML")
            delivered = True
        except Exception as e:
            print(f"Deposit admin notify error: {e}")

    if not delivered:
        await db.set_deposit_status(req_id, "rejected")
        await callback.message.edit_caption(caption="⚠️ Could not submit your request right now. Please try again later.", reply_markup=None)
        await callback.answer()
        return

    await callback.message.edit_caption(
        caption="✅ Your payment request has been successfully submitted. Please wait for admin's approval.",
        reply_markup=None
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("dep_ok_"))
async def deposit_approve(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    req_id = int(callback.data.split("_")[2])
    req = await db.set_deposit_status(req_id, "approved")
    if not req:
        await callback.answer("Already processed.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    amount = req["amount"]
    await db.update_balance(req["user_id"], amount)
    await db.save_payment_record(req["user_id"], amount, "DEPOSIT", "TOPUP_SUCCESS")

    try:
        await bot.send_message(
            req["user_id"],
            f"✅ **ᴅᴇᴘᴏꜱɪᴛ ᴀᴘᴘʀᴏᴠᴇᴅ!**\n\nAdded **₹{amount:.2f}** to your wallet balance.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await callback.message.edit_caption(
            caption=deposit_admin_caption(req, "✅ <b>APPROVED</b> (balance added)"),
            reply_markup=None, parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Approved ✅")

@dp.callback_query(F.data.startswith("dep_no_"))
async def deposit_reject(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    req_id = int(callback.data.split("_")[2])
    req = await db.set_deposit_status(req_id, "rejected")
    if not req:
        await callback.answer("Already processed.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    try:
        await bot.send_message(
            req["user_id"],
            "❌ **ᴅᴇᴘᴏꜱɪᴛ ʀᴇᴊᴇᴄᴛᴇᴅ.**\n\n"
            "Your deposit request could not be verified. No balance was added. "
            "If you think this is a mistake, please contact support.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

    try:
        await callback.message.edit_caption(
            caption=deposit_admin_caption(req, "❌ <b>REJECTED</b>"),
            reply_markup=None, parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Rejected ❌")

@dp.message(Command("referral"))
@dp.callback_query(F.data == "referral_menu")
async def referral_handler(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    user = await db.get_user(user_id)
    bot_user = await bot.get_me()
    ref_link = f"https://t.me/{bot_user.username}?start=ref_{user_id}"
    ref_count = user.get("referral_count", 0)

    text = (
        f"👥 **ʀᴇꜰᴇʀʀᴀʟ ᴘʀᴏɢʀᴀᴍ**\n\n"
        f"Invite your friends and earn **₹0.001** for each referral when they join and verify their membership!\n\n"
        f"🔗 **ʏᴏᴜʀ ʀᴇꜰᴇʀʀᴀʟ ʟɪɴᴋ:**\n`{ref_link}`\n\n"
        f"📊 Total Verified Referrals: **{ref_count}**"
    )
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    else:
        await event.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.message(Command("promo"))
@dp.callback_query(F.data == "redeem_promo_menu")
async def redeem_promo_prompt(event: types.Message | types.CallbackQuery, state: FSMContext = None):
    if isinstance(event, types.CallbackQuery):
        if state is None:
            await event.answer()
            return
        await state.set_state(PromoCodeState.waiting_for_code)
        await event.message.edit_text(
            "🎁 **ʀᴇᴅᴇᴇᴍ ᴘʀᴏᴍᴏ ᴄᴏᴅᴇ**\n\nPlease send your promo code below:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="back_home")]]),
            parse_mode="Markdown"
        )
    else:
        args = event.text.split()
        if len(args) == 2:
            code = args[1]
            success, msg = await db.use_promo_code(event.from_user.id, code)
            await event.answer(msg)
        else:
            await event.answer("Usage: `/promo YOUR_CODE` or use the menu.", parse_mode="Markdown")

@dp.message(PromoCodeState.waiting_for_code)
async def process_promo_code(message: types.Message, state: FSMContext):
    code = (message.text or "").strip()
    success, msg = await db.use_promo_code(message.from_user.id, code)
    await state.clear()
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "shop")
async def show_shop(callback: types.CallbackQuery):
    categories = await db.get_categories_with_counts()
    if not categories:
        await callback.message.edit_text("❌ No categories available right now.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]))
        return

    kb = []
    for c_id, c_name, available_count in categories:
        kb.append([InlineKeyboardButton(text=sc(f"📂 {c_name} ({available_count} Available)"), callback_data=f"cat_{c_id}")])
    kb.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")])

    await callback.message.edit_text("🛍️ **ꜱᴇʟᴇᴄᴛ ᴀ ᴄᴀᴛᴇɢᴏʀʏ:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

SHOP_PAGE_SIZE = 5

@dp.callback_query(F.data.startswith("cat_"))
async def show_category_items(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    cat_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0

    accounts = await db.get_available_accounts(cat_id)

    if not accounts:
        await callback.message.edit_text("❌ No accounts available in this category.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="shop")]]))
        return

    total_pages = (len(accounts) + SHOP_PAGE_SIZE - 1) // SHOP_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * SHOP_PAGE_SIZE
    page_items = accounts[start:start + SHOP_PAGE_SIZE]

    kb = [[InlineKeyboardButton(text=sc(f"{display_name} - ₹{price}"), callback_data=f"select_acc_{acc_id}")] for acc_id, display_name, price in page_items]

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text=sc("⬅️ Prev"), callback_data=f"cat_{cat_id}_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text=sc("Next ➡️"), callback_data=f"cat_{cat_id}_{page + 1}"))
    if nav_row:
        kb.append(nav_row)

    kb.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data="shop")])

    await callback.message.edit_text(
        sc(f"📦 **Available Accounts (Page {page + 1}/{total_pages}):**"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("select_acc_"))
async def show_purchase_confirmation(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    account = await db.get_account_by_id(acc_id)

    if not account or account.get("is_sold") == 1:
        await callback.answer("❌ This account is no longer available.", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    user_balance = user['balance']
    price = account['price']
    after_balance = user_balance - price

    text = (
        f"🛒 **ᴄᴏɴꜰɪʀᴍ ᴘᴜʀᴄʜᴀꜱᴇ**\n\n"
        f"📦 Item: {account.get('display_name', 'Account')}\n"
        f"💰 Price: `₹{price:.2f}`\n"
        f"💳 Current Balance: `₹{user_balance:.3f}`\n"
        f"💳 Balance After Purchase: `₹{after_balance:.3f}`\n\n"
        f"Do you want to proceed?"
    )

    kb = [
        [InlineKeyboardButton(text=sc("✅ Confirm Purchase"), callback_data=f"do_buy_{acc_id}")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data=f"cat_{account['category_id']}")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

def build_account_details_text(account: dict) -> str:
    return (
        f"✅ **ᴀᴄᴄᴏᴜɴᴛ ᴅᴇᴛᴀɪʟꜱ**\n\n"
        f"📱 **Number:** `{account.get('phone_number', 'Unknown')}`\n"
        f"🆔 **User ID:** `{account.get('telegram_user_id') or 'Unknown'}`\n"
        f"🔑 **Session String:**\n`{account.get('session_string')}`\n\n"
        f"🔐 **2-Step Verification:** `{'ON' if account.get('two_step_enabled') else 'OFF'}`\n"
        f"🔑 **2-Step Password:** `{account.get('two_step') or 'None'}`\n\n"
        f"👇 Use the buttons below to get your OTP code or manage devices:"
    )

def build_account_details_kb(acc_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=sc("📥 Get Code"), callback_data=f"get_otp_{acc_id}"),
            InlineKeyboardButton(text=sc("📱 Devices"), callback_data=f"devices_{acc_id}")
        ],
        [InlineKeyboardButton(text=sc("🏠 Main Menu"), callback_data="back_home")]
    ])

@dp.callback_query(F.data.startswith("viewacc_"))
async def view_account_details(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[1])
    account = await db.get_account_by_id(acc_id)

    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return
    if account.get("sold_to") != callback.from_user.id:
        await callback.answer("❌ This account does not belong to you.", show_alert=True)
        return

    await callback.message.edit_text(
        build_account_details_text(account),
        reply_markup=build_account_details_kb(acc_id),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("do_buy_"))
async def process_purchase(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    account = await db.get_account_by_id(acc_id)
    if not account or account.get("is_sold") == 1:
        await callback.answer("❌ This account is no longer available.", show_alert=True)
        return

    status, phone, session, price, two_step, tg_user_id, two_step_enabled = await db.buy_account_safely(user_id, acc_id)

    if status == "success":
        asyncio.create_task(start_userbot_for_account(phone, session, bot, user_id))

        masked_phone = mask_phone_number(phone)
        masked_user = str(user_id)[:2] + "***" + str(user_id)[-3:] if len(str(user_id)) > 5 else "***"
        bot_user = await bot.get_me()
        bot_username = bot_user.username
        item_name = account.get('display_name', 'Account')

        channel_text = (
            f"🚀 **ɴᴇᴡ ᴀᴄᴄᴏᴜɴᴛ ꜱᴏʟᴅ!**\n\n"
            f"👤 User: `{masked_user}`\n"
            f"📦 Item: `{item_name}`\n"
            f"📍 Region: `{item_name}`\n"
            f"📱 Number: `{masked_phone}`\n"
            f"💰 Price: `₹{price:.1f}`\n"
            f"⚡ Status: Verified & Delivered\n\n"
            f"🤖 Always use @{bot_username}"
        )

        LOG_CHANNEL = "@zyXzo"
        try:
            await bot.send_message(chat_id=LOG_CHANNEL, text=channel_text, parse_mode="Markdown")
        except Exception as e:
            print(f"Channel log error: {e}")

        details_text = build_account_details_text({
            "phone_number": phone,
            "telegram_user_id": tg_user_id,
            "session_string": session,
            "two_step": two_step,
            "two_step_enabled": two_step_enabled,
        }).replace("✅ **ᴀᴄᴄᴏᴜɴᴛ ᴅᴇᴛᴀɪʟꜱ**", "✅ **ᴘᴜʀᴄʜᴀꜱᴇ ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟ!**", 1)

        await callback.message.edit_text(details_text, reply_markup=build_account_details_kb(acc_id), parse_mode="Markdown")

    elif status == "low_balance":
        await callback.answer("❌ Insufficient balance! Please add funds to your wallet.", show_alert=True)
    else:
        await callback.answer("❌ Sorry, this account was already sold.", show_alert=True)

async def render_devices(callback: types.CallbackQuery, acc_id: int, phone: str):
    devices, error = await get_devices(phone)
    if error:
        await callback.answer(f"⚠️ {error}", show_alert=True)
        return
    if not devices:
        await callback.answer("No active sessions found.", show_alert=True)
        return

    lines = ["📱 **ʟᴏɢɢᴇᴅ-ɪɴ ᴅᴇᴠɪᴄᴇꜱ**\n"]
    kb_rows = []
    for idx, d in enumerate(devices, start=1):
        if d["current"]:
            lines.append(f"**{idx}. Device {idx}** - Bot Session (current)")
            kb_rows.append([InlineKeyboardButton(
                text=f"⚠️ Terminate - Device {idx} (Bot Session)",
                callback_data=f"trmbot_{acc_id}"
            )])
        else:
            extra = f" ({d['country']})" if d["country"] else ""
            lines.append(f"**{idx}. Device {idx}** - {d['label']}{extra}")
            kb_rows.append([InlineKeyboardButton(
                text=f"❌ Terminate - Device {idx}",
                callback_data=f"trm_{acc_id}_{d['hash']}"
            )])

    kb_rows.append([InlineKeyboardButton(text=sc("🔄 Refresh"), callback_data=f"devices_{acc_id}")])
    kb_rows.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data=f"viewacc_{acc_id}")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("devices_"))
async def devices_handler(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[1])
    account = await db.get_account_by_id(acc_id)

    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return

    if account.get("sold_to") != callback.from_user.id:
        await callback.answer("❌ This account does not belong to you.", show_alert=True)
        return

    phone = account.get('phone_number')
    client = active_clients.get(phone)

    if not client:
        asyncio.create_task(start_userbot_for_account(phone, account['session_string'], bot, callback.from_user.id))
        await callback.answer("⏳ Initializing userbot, please tap 'Devices' again after 5 seconds.", show_alert=True)
        return

    await render_devices(callback, acc_id, phone)
    await callback.answer()

@dp.callback_query(F.data.startswith("trm_"))
async def terminate_device_handler(callback: types.CallbackQuery):
    parts = callback.data.split("_", 2)
    acc_id = int(parts[1])
    auth_hash = int(parts[2])

    account = await db.get_account_by_id(acc_id)
    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return

    if account.get("sold_to") != callback.from_user.id:
        await callback.answer("❌ This account does not belong to you.", show_alert=True)
        return

    phone = account.get('phone_number')
    ok, error = await terminate_device(phone, auth_hash)
    if not ok:
        await callback.answer(f"⚠️ {error}", show_alert=True)
        return

    await callback.answer("✅ Device terminated!", show_alert=True)
    await render_devices(callback, acc_id, phone)

@dp.callback_query(F.data.startswith("trmbot_"))
async def terminate_bot_session_confirm(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[1])
    account = await db.get_account_by_id(acc_id)

    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return
    if account.get("sold_to") != callback.from_user.id:
        await callback.answer("❌ This account does not belong to you.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=sc("✅ Yes, terminate it"), callback_data=f"trmbotok_{acc_id}")],
        [InlineKeyboardButton(text=sc("❌ Cancel"), callback_data=f"devices_{acc_id}")]
    ])
    await callback.message.edit_text(
        "⚠️ **ᴡᴀʀɴɪɴɢ**\n\n"
        "This will permanently remove our bot's access to this account.\n\n"
        "After this, **Get Code** and **Devices** will stop working for this account — "
        "the account will be fully and exclusively yours, and no one else will be able to log in to it remotely.\n\n"
        "This action **cannot be undone**. Continue?",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("trmbotok_"))
async def terminate_bot_session_execute(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[1])
    account = await db.get_account_by_id(acc_id)

    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return
    if account.get("sold_to") != callback.from_user.id:
        await callback.answer("❌ This account does not belong to you.", show_alert=True)
        return

    phone = account.get('phone_number')
    ok, error = await terminate_bot_session(phone)
    if not ok:
        await callback.answer(f"⚠️ {error}", show_alert=True)
        return

    await callback.message.edit_text(
        "✅ **ᴅᴏɴᴇ!**\n\nOur bot's session has been terminated. This account is now fully and exclusively yours.",
        parse_mode="Markdown"
    )
    await callback.answer("✅ Bot session terminated!", show_alert=True)

@dp.callback_query(F.data.startswith("get_otp_"))
async def get_otp_handler(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    account = await db.get_account_by_id(acc_id)

    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return

    # OWNERSHIP CHECK: only the buyer can fetch the OTP
    if account.get("sold_to") != callback.from_user.id:
        await callback.answer("❌ This account does not belong to you.", show_alert=True)
        return

    phone = account.get('phone_number')
    client = active_clients.get(phone)

    if not client:
        asyncio.create_task(start_userbot_for_account(phone, account['session_string'], bot, callback.from_user.id))
        await callback.answer("⏳ Initializing userbot, please click 'Get Code' again after 5 seconds.", show_alert=True)
        return

    try:
        # look at the latest few messages from Telegram and take the newest one that has a code
        messages = await client.get_messages(777000, limit=5)
        otp_code = None
        for m in messages:
            match = re.search(r'\b\d{4,6}\b', m.message or "")
            if match:
                otp_code = match.group(0)
                break

        if otp_code:
            await callback.message.answer(f"Your code is - `{otp_code}`", parse_mode="Markdown")
            await callback.answer("OTP sent successfully! ✅")
        else:
            await callback.answer("⚠ No OTP received yet. Please try again later.", show_alert=True)
    except Exception as e:
        await callback.answer(f"⚠️ Error fetching OTP: {e}", show_alert=True)

@dp.message(Command("myorders"))
@dp.callback_query(F.data == "my_orders")
async def my_orders_handler(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    payments = await db.get_user_payments(user_id)
    purchases = [p for p in payments if p['status'] == 'SPEND_BUY_ACCOUNT']

    text = "📦 **ʏᴏᴜʀ ᴏʀᴅᴇʀꜱ ʜɪꜱᴛᴏʀʏ:**\n\n"
    if not purchases:
        text += "You haven't purchased any accounts yet."
    else:
        for idx, p in enumerate(purchases, 1):
            text += f"{idx}. Amount: `₹{p['amount']}` | Date: `{p['timestamp']}`\n"

    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(Command("privacy"))
@dp.callback_query(F.data == "privacy_policy")
async def privacy_policy_handler(event: types.Message | types.CallbackQuery):
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(PRIVACY_POLICY_TEXT, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(PRIVACY_POLICY_TEXT, reply_markup=markup)

@dp.callback_query(F.data == "support")
async def support_handler(callback: types.CallbackQuery):
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]
    await callback.message.edit_text("🆘 **ꜱᴜᴘᴘᴏʀᴛ:**\n\nFor any issues or questions, please contact the owner.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "my_profile")
async def my_profile_handler(callback: types.CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="back_home")]]
    await callback.message.edit_text(
        f"👤 **ʏᴏᴜʀ ᴘʀᴏꜰɪʟᴇ**\n\n"
        f"🆔 User ID: `{callback.from_user.id}`\n"
        f"💵 Balance: **₹{user['balance']:.3f}**\n"
        f"✅ Verified: **{'Yes' if user['is_verified'] == 1 else 'No'}**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

# ======================= ADMIN =======================

@dp.message(Command("admin"))
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    if not is_admin(user_id):
        return

    kb = [
        [InlineKeyboardButton(text=sc("📊 Detailed Analytics & Stats"), callback_data="admin_stats")],
        [InlineKeyboardButton(text=sc("🎁 Create Promo Code"), callback_data="admin_add_promo_prompt")],
        [InlineKeyboardButton(text=sc("🔗 Supplier API Settings"), callback_data="admin_supplier_menu")],
        [InlineKeyboardButton(text=sc("🩺 Check Account Health"), callback_data="admin_health_check")],
        [InlineKeyboardButton(text=sc("📂 Manage Categories"), callback_data="admin_cats")],
        [InlineKeyboardButton(text=sc("➕ Add Account"), callback_data="admin_add_acc")],
        [InlineKeyboardButton(text=sc("ℹ️ Info"), callback_data="admin_info")],
        [InlineKeyboardButton(text=sc("🗑️ Delete Stock"), callback_data="admin_del_acc_list")],
        [InlineKeyboardButton(text=sc("📦 Sales History"), callback_data="admin_sales_history")],
        [InlineKeyboardButton(text=sc("👥 User Management"), callback_data="admin_users")],
        [InlineKeyboardButton(text=sc("📢 Broadcast"), callback_data="admin_broadcast")],
        [InlineKeyboardButton(text=sc("🔙 Main Menu"), callback_data="back_home")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    text = "⚙️ **ᴀᴅᴍɪɴ ᴄᴏɴᴛʀᴏʟ ᴘᴀɴᴇʟ**\n\nSelect an option below:"

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    else:
        await event.answer(text, reply_markup=markup, parse_mode="Markdown")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    stats = await db.get_detailed_stock_stats()
    text = (
        f"📊 **ᴅᴇᴛᴀɪʟᴇᴅ ᴀɴᴀʟʏᴛɪᴄꜱ & ꜱᴛᴏᴄᴋ ꜱᴛᴀᴛᴜꜱ:**\n\n"
        f"• Available Accounts: `{stats['available']}`\n"
        f"• Sold Accounts: `{stats['sold']}`\n"
        f"• Total Registered Users: `{stats['users']}`\n"
        f"• Total Revenue Generated: `₹{stats['revenue']:.2f}`\n"
    )
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_add_promo_prompt")
async def admin_add_promo_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(CreatePromoState.waiting_for_code_name)
    await callback.message.edit_text(
        "🎁 Enter the promo code name (e.g., WELCOME):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_panel")]])
    )

@dp.message(CreatePromoState.waiting_for_code_name)
async def get_promo_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(promo_code=message.text.strip())
    await state.set_state(CreatePromoState.waiting_for_code_amount)
    await message.answer("💵 Enter the discount/bonus amount in ₹ (e.g., 0.05):")

@dp.message(CreatePromoState.waiting_for_code_amount)
async def get_promo_amount(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        amount = float(message.text)
    except ValueError:
        await message.answer("❌ Invalid amount. Please enter a valid number:")
        return

    data = await state.get_data()
    code = data.get("promo_code")
    await db.add_promo_code(code, amount)
    await state.clear()
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]]
    await message.answer(f"✅ Promo code `{code}` created successfully with value `₹{amount}`!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "admin_supplier_menu")
async def admin_supplier_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    config = await db.get_supplier_config()
    text = (
        f"🔗 **ꜱᴜᴘᴘʟɪᴇʀ ᴀᴘɪ ɪɴᴛᴇɢʀᴀᴛɪᴏɴ ꜱᴇᴛᴛɪɴɢꜱ**\n\n"
        f"• Status: `{'Active' if config['is_active'] else 'Inactive / Modular Placeholder'}`\n"
        f"• API URL: `{config['api_url'] if config['api_url'] else 'Not Set'}`\n\n"
        f"Use this section to configure external supplier auto-refill hooks when needed."
    )
    kb = [
        [InlineKeyboardButton(text=sc("✏️ Configure API URL & Key"), callback_data="config_supplier_step")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "config_supplier_step")
async def config_supplier_step(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(SupplierConfigState.waiting_for_url)
    await callback.message.edit_text("🔗 Send Supplier API Endpoint URL:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_supplier_menu")]]))

@dp.message(SupplierConfigState.waiting_for_url)
async def supplier_get_url(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(supplier_url=message.text.strip())
    await state.set_state(SupplierConfigState.waiting_for_key)
    await message.answer("🔑 Send Supplier API Key / Token:")

@dp.message(SupplierConfigState.waiting_for_key)
async def supplier_get_key(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    key = message.text.strip()
    data = await state.get_data()
    url = data.get("supplier_url")
    await db.set_supplier_config(url, key, is_active=True)
    await state.clear()
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]]
    await message.answer("✅ Supplier API configuration saved successfully!", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "admin_health_check")
async def admin_health_check(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    accounts = await db.get_all_unsold_accounts()
    total_unsold = len(accounts)
    text = (
        f"🩺 **ᴀᴄᴄᴏᴜɴᴛ ʜᴇᴀʟᴛʜ ᴄʜᴇᴄᴋ ʀᴇᴘᴏʀᴛ**\n\n"
        f"• Total Unsold Accounts in Stock: `{total_unsold}`\n"
        f"• Session Validation Status: All sessions are registered in DB.\n"
    )
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_info")
async def admin_info_categories(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    categories = await db.get_categories_with_counts()
    if not categories:
        await callback.message.edit_text(
            "❌ No categories available.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]])
        )
        return

    kb = [[InlineKeyboardButton(text=sc(f"📂 {name}"), callback_data=f"infostat_{cid}")] for cid, name, _count in categories]
    kb.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")])
    await callback.message.edit_text(
        "ℹ️ **ꜱᴇʟᴇᴄᴛ ᴀ ᴄᴀᴛᴇɢᴏʀʏ ᴛᴏ ᴠɪᴇᴡ ᴀᴄᴄᴏᴜɴᴛ ɪɴꜰᴏ:**",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("infostat_"))
async def admin_info_status_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    cat_id = int(callback.data.split("_")[1])

    accounts = await db.get_accounts_by_category_full(cat_id)
    if not accounts:
        await callback.message.edit_text(
            "❌ No accounts in this category.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_info")]])
        )
        return

    available_count = sum(1 for a in accounts if a.get("is_sold") != 1)
    sold_count = sum(1 for a in accounts if a.get("is_sold") == 1)

    kb = [
        [InlineKeyboardButton(text=sc(f"✅ Available ({available_count})"), callback_data=f"infolist_{cat_id}_avail_0")],
        [InlineKeyboardButton(text=sc(f"❌ Sold ({sold_count})"), callback_data=f"infolist_{cat_id}_sold_0")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_info")]
    ]
    await callback.message.edit_text(
        "ℹ️ **ᴄʜᴏᴏꜱᴇ ᴡʜɪᴄʜ ᴀᴄᴄᴏᴜɴᴛꜱ ᴛᴏ ᴠɪᴇᴡ:**",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

ADMIN_INFO_PAGE_SIZE = 3

@dp.callback_query(F.data.startswith("infolist_"))
async def admin_info_accounts(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    parts = callback.data.split("_")
    cat_id = int(parts[1])
    status = parts[2]  # "avail" or "sold"
    page = int(parts[3]) if len(parts) > 3 else 0

    all_accounts = await db.get_accounts_by_category_full(cat_id)
    if status == "sold":
        accounts = [a for a in all_accounts if a.get("is_sold") == 1]
        label = "❌ Sold"
    else:
        accounts = [a for a in all_accounts if a.get("is_sold") != 1]
        label = "✅ Available"

    if not accounts:
        await callback.message.edit_text(
            f"❌ No {label} accounts in this category.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data=f"infostat_{cat_id}")]])
        )
        return

    total_pages = (len(accounts) + ADMIN_INFO_PAGE_SIZE - 1) // ADMIN_INFO_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * ADMIN_INFO_PAGE_SIZE
    page_items = accounts[start:start + ADMIN_INFO_PAGE_SIZE]

    lines = [sc(f"ℹ️ **{label} Accounts (Page {page + 1}/{total_pages})**\n")]
    for idx, acc in enumerate(page_items, start=1):
        sold_to = f"\n   👤 Buyer ID: `{acc.get('sold_to')}`" if status == "sold" and acc.get("sold_to") else ""
        lines.append(
            f"**{start + idx}. {acc.get('display_name', 'Account')}**\n"
            f"   🆔 Acc ID: `{acc['account_id']}`\n"
            f"   📱 Phone: `{acc.get('phone_number', 'Unknown')}`\n"
            f"   🆔 TG User ID: `{acc.get('telegram_user_id') or 'Unknown'}`\n"
            f"   💰 Price: ₹{acc.get('price')}\n"
            f"   🔐 2-Step: {'ON' if acc.get('two_step_enabled') else 'OFF'}{sold_to}\n"
        )

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text=sc("⬅️ Prev"), callback_data=f"infolist_{cat_id}_{status}_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text=sc("Next ➡️"), callback_data=f"infolist_{cat_id}_{status}_{page + 1}"))

    kb_rows = []
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data=f"infostat_{cat_id}")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "admin_cats")
async def admin_cats_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    categories = await db.get_categories()
    kb = [[InlineKeyboardButton(text=sc(f"✏️ Edit: {c[1]}"), callback_data=f"editcat_{c[0]}")] for c in categories]
    kb.append([InlineKeyboardButton(text=sc("➕ Add New Category"), callback_data="admin_add_cat")])
    kb.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")])

    await callback.message.edit_text("📂 **ᴍᴀɴᴀɢᴇ ᴄᴀᴛᴇɢᴏʀɪᴇꜱ:**\n\nClick a category to edit its name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("editcat_"))
async def edit_category_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(editing_cat_id=cat_id)
    await state.set_state(EditCategoryState.waiting_for_new_name)
    await callback.message.edit_text("✍ Send new category name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_cats")]]))

@dp.message(EditCategoryState.waiting_for_new_name)
async def save_edited_category(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    cat_id = data.get("editing_cat_id")
    new_name = message.text

    await db.update_category_name(cat_id, new_name)
    await state.clear()
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_cats")]]
    await message.answer(f"✅ Category successfully renamed to '{new_name}'!", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "admin_users")
async def admin_users_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    kb = [
        [InlineKeyboardButton(text=sc("🚫 Ban a User"), callback_data="admin_ban_user")],
        [InlineKeyboardButton(text=sc("✅ Unban a User"), callback_data="admin_unban_user")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]
    ]
    await callback.message.edit_text("👥 **ᴜꜱᴇʀ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_ban_user")
async def admin_ban_user_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BanUserState.waiting_for_user_id)
    await state.update_data(ban_action=1)
    await callback.message.edit_text("✍️ Send the **User ID** of the user you want to ban:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_users")]]), parse_mode="Markdown")

# NOTE: the original code had no handler for admin_unban_user; added here.
@dp.callback_query(F.data == "admin_unban_user")
async def admin_unban_user_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BanUserState.waiting_for_user_id)
    await state.update_data(ban_action=0)
    await callback.message.edit_text("✍️ Send the **User ID** of the user you want to unban:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_users")]]), parse_mode="Markdown")

@dp.message(BanUserState.waiting_for_user_id)
async def execute_ban_user(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        target_id = int(message.text)
    except (ValueError, TypeError):
        await message.answer("❌ Invalid User ID. Please send numbers only.")
        return

    data = await state.get_data()
    action = data.get("ban_action", 1)
    await db.set_user_ban_status(target_id, action)
    await state.clear()
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_users")]]
    if action == 1:
        await message.answer(f"✅ User `{target_id}` has been successfully banned!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await message.answer(f"✅ User `{target_id}` has been unbanned!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "admin_sales_history")
async def admin_sales_history(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    sales = await db.get_all_sales_history(limit=10)
    text = "📦 **ʀᴇᴄᴇɴᴛ ꜱᴀʟᴇꜱ ʟᴏɢꜱ:**\n\n"
    if not sales:
        text += "No sales records found."
    else:
        for s in sales:
            text += f"• User: `{s['user_id']}` | Amount: `₹{s['amount']}` | Date: `{s['timestamp']}`\n"

    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text("📢 Send the message/text/photo to broadcast to all users:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_panel")]]))

@dp.message(BroadcastState.waiting_for_message)
async def execute_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    users = await db.get_all_user_ids()
    sent = 0
    failed = 0

    status_msg = await message.answer("📢 Broadcast started...")
    for uid in users:
        try:
            await message.send_copy(chat_id=uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(f"✅ **Broadcast Completed!**\n\n- Sent: {sent}\n- Failed: {failed}", parse_mode="Markdown")

@dp.callback_query(F.data == "admin_del_acc_list")
async def admin_del_acc_list(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    accounts = await db.get_all_unsold_accounts()
    if not accounts:
        await callback.message.edit_text(
            "❌ No accounts available to delete.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]])
        )
        return

    kb = [[InlineKeyboardButton(text=sc(f"❌ Delete {disp_name} (₹{price})"), callback_data=f"delacc_{acc_id}")] for acc_id, disp_name, price in accounts]
    kb.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")])

    await callback.message.edit_text("🗑️ **Delete Account:**\n\nClick an account to delete it:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("delacc_"))
async def delete_account_action(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    acc_id = int(callback.data.split("_")[1])
    await db.delete_account(acc_id)
    await callback.answer("✅ Account successfully deleted!", show_alert=True)
    await admin_del_acc_list(callback)

@dp.callback_query(F.data == "admin_add_cat")
async def admin_add_cat(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(AddCategoryState.waiting_for_name)
    await callback.message.edit_text(
        "✍️ Send category name:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_cats")]])
    )

@dp.message(AddCategoryState.waiting_for_name)
async def save_cat(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await db.add_category(message.text)
    await state.clear()
    kb = [[InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_cats")]]
    await message.answer(f"✅ Category '{message.text}' added successfully!", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(Command("add"))
@dp.callback_query(F.data == "admin_add_acc")
async def admin_add_acc(event: types.Message | types.CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    if not is_admin(user_id):
        return

    categories = await db.get_categories()
    if not categories:
        text = "❌ Please create a category first!"
        if isinstance(event, types.CallbackQuery):
            await event.message.edit_text(text)
        else:
            await event.answer(text)
        return

    kb = [[InlineKeyboardButton(text=sc(c[1]), callback_data=f"selcat_{c[0]}")] for c in categories]
    kb.append([InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")])
    markup = InlineKeyboardMarkup(inline_keyboard=kb)

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text("📂 Select category:", reply_markup=markup)
    else:
        await event.answer("📂 Select category:", reply_markup=markup)

def admin_cancel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=sc("❌ Cancel"), callback_data="admin_add_cancel")]])

@dp.callback_query(F.data == "admin_add_cancel")
async def admin_add_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await admin_panel(callback)

@dp.callback_query(F.data.startswith("selcat_"))
async def sel_cat(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.update_data(category_id=int(callback.data.split("_")[1]))
    await state.set_state(AddItemState.waiting_for_display_name)
    await callback.message.edit_text(
        "✍️ Send display name for button (e.g., 🇮🇳 India - ₹25):",
        reply_markup=admin_cancel_kb()
    )

@dp.message(AddItemState.waiting_for_display_name)
async def get_display_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(display_name=message.text)
    await state.set_state(AddItemState.waiting_for_price)
    await message.answer("💵 Send price in ₹ (e.g., 20.0):", reply_markup=admin_cancel_kb())

@dp.message(AddItemState.waiting_for_price)
async def get_price(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        price = float(message.text)
    except ValueError:
        await message.answer("❌ Invalid price. Enter a valid number:", reply_markup=admin_cancel_kb())
        return

    await state.update_data(price=price)
    await state.set_state(AddItemState.waiting_for_session)
    await message.answer(
        "🔑 Send the account session. Any of these work:\n"
        "• A Telethon session string (as text)\n"
        "• A Pyrogram session string (as text)\n"
        "• A Telethon or Pyrogram `.session` file\n"
        "• A `.zip` file containing a `.session` file or a tdata folder",
        parse_mode="Markdown",
        reply_markup=admin_cancel_kb()
    )

@dp.message(AddItemState.waiting_for_session)
async def get_session(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    checking_msg = await message.answer("🔍 Reading session, please wait...")
    work_dir = tempfile.mkdtemp(prefix="sess_")
    info, error = None, None

    try:
        if message.document:
            fname = (message.document.file_name or "").lower()
            local_path = os.path.join(work_dir, message.document.file_name or "upload.bin")
            await bot.download(message.document, destination=local_path)

            if fname.endswith(".session"):
                info, error = await process_uploaded_session("file", local_path, work_dir)
            elif fname.endswith(".zip"):
                info, error = await process_uploaded_session("zip", local_path, work_dir)
            else:
                error = "Unsupported file type. Send a session string, a .session file, or a .zip file."
        else:
            text = (message.text or "").strip()
            if not text:
                error = "Please send a session string, a .session file, or a .zip file."
            else:
                # try as a Telethon string first, then as a Pyrogram string
                info, error = await process_uploaded_session("telethon_string", text, work_dir)
                if error:
                    info2, error2 = await process_uploaded_session("pyrogram_string", text, work_dir)
                    if not error2:
                        info, error = info2, None
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    if error or not info:
        await checking_msg.edit_text(
            f"❌ Session invalid. Account cannot be added.\n\n"
            f"Reason: {error}\n\n"
            f"Please send a valid session (string, .session file, or .zip):",
            reply_markup=admin_cancel_kb()
        )
        return

    await state.update_data(
        session_string=info["session_string"],
        phone_number=info["phone_number"],
        telegram_user_id=info["user_id"],
        two_step_enabled=info["two_step_enabled"],
    )

    status_line = "ON" if info["two_step_enabled"] else "OFF"
    await state.set_state(AddItemState.waiting_for_two_step)
    await checking_msg.edit_text(
        f"✅ Session verified!\n\n"
        f"📱 Phone: `{info['phone_number']}`\n"
        f"🆔 User ID: `{info['user_id']}`\n"
        f"🔐 2-Step Verification (detected): **{status_line}**\n\n"
        f"Please send the 2-Step password for this account (or send `skip` if you don't have one):",
        parse_mode="Markdown",
        reply_markup=admin_cancel_kb()
    )

@dp.message(AddItemState.waiting_for_two_step)
async def get_two_step(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    raw = (message.text or "").strip()
    two_step = "" if raw.lower() == "skip" else raw
    await state.update_data(two_step=two_step)
    await finalize_add_account(message, state)

async def finalize_add_account(message: types.Message, state: FSMContext):
    data = await state.get_data()
    await db.add_account(
        data["category_id"],
        data["display_name"],
        data["phone_number"],
        data["session_string"],
        data["price"],
        two_step=data.get("two_step", ""),
        telegram_user_id=data.get("telegram_user_id"),
        two_step_enabled=data.get("two_step_enabled", False),
    )
    await state.clear()
    kb = [
        [InlineKeyboardButton(text=sc("➕ Add Another"), callback_data="admin_add_acc")],
        [InlineKeyboardButton(text=sc("🔙 Back"), callback_data="admin_panel")]
    ]
    await message.answer("✅ Account added successfully!", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(Command("dd"))
async def cmd_add_balance(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Usage: `/dd <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        target_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ Invalid user_id or amount.")
        return
    if amount <= 0:
        await message.answer("❌ Amount must be greater than 0.")
        return

    await db.update_balance(target_id, amount)
    await db.save_payment_record(target_id, amount, "ADMIN", "ADMIN_CREDIT")
    await message.answer(f"✅ Added ₹{amount:.2f} to user `{target_id}`'s balance.", parse_mode="Markdown")

    try:
        await bot.send_message(
            target_id,
            f"✅ **ʙᴀʟᴀɴᴄᴇ ᴀᴅᴅᴇᴅ**\n\nAdmin has added **₹{amount:.2f}** to your wallet balance.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

@dp.message(Command("ss"))
async def cmd_deduct_balance(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Usage: `/ss <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        target_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ Invalid user_id or amount.")
        return
    if amount <= 0:
        await message.answer("❌ Amount must be greater than 0.")
        return

    await db.update_balance(target_id, -amount)
    await db.save_payment_record(target_id, amount, "ADMIN", "ADMIN_DEBIT")
    await message.answer(f"✅ Deducted ₹{amount:.2f} from user `{target_id}`'s balance.", parse_mode="Markdown")

    try:
        await bot.send_message(
            target_id,
            f"⚠️ **ʙᴀʟᴀɴᴄᴇ ᴅᴇᴅᴜᴄᴛᴇᴅ**\n\nAdmin has deducted **₹{amount:.2f}** from your wallet balance.",
            parse_mode="Markdown"
        )
    except Exception:
        pass

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(callback)

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
