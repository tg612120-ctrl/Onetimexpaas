import asyncio
import logging
import sys
import re
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, BotCommandScopeDefault, LabeledPrice, PreCheckoutQuery, Message

from config import BOT_TOKEN, OWNER_ID, REQUIRED_CHANNELS
import database as db
from userbot import start_userbot_for_account, active_clients

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

CO_OWNER_ID = 0

STAR_RATE = 1.3
MIN_STARS = 15
MAX_STARS = 10000   # Telegram invoice limit


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
    waiting_for_phone = State()
    waiting_for_session = State()
    waiting_for_price = State()

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
    ]
    await bot_instance.set_my_commands(user_commands, scope=BotCommandScopeDefault())

    owner_commands = [
        BotCommand(command="start", description="Start the bot 🚀"),
        BotCommand(command="wallet", description="Open your wallet 💰"),
        BotCommand(command="myorders", description="View your purchase history 📦"),
        BotCommand(command="promo", description="Redeem promo code 🎁"),
        BotCommand(command="referral", description="Invite & earn bonus 👥"),
        BotCommand(command="admin", description="Open Admin Panel ⚙"),
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
            InlineKeyboardButton(text="🛒 Buy Now", callback_data="shop"),
            InlineKeyboardButton(text="💳 Wallet", callback_data="wallet")
        ],
        [
            InlineKeyboardButton(text="📦 My Orders", callback_data="my_orders"),
            InlineKeyboardButton(text="🎁 Redeem Promo", callback_data="redeem_promo_menu")
        ],
        [
            InlineKeyboardButton(text="👥 Referral", callback_data="referral_menu"),
            InlineKeyboardButton(text="👤 Profile", callback_data="my_profile")
        ],
        [
            InlineKeyboardButton(text="🆘 Support", callback_data="support")
        ]
    ]
    if is_admin(user_id):
        kb.append([InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="admin_panel")])

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
        kb.append([InlineKeyboardButton(text=f"📢 Join Channel {idx}", url=f"https://t.me/{ch.lstrip('@')}")])
    kb.append([InlineKeyboardButton(text="✅ Verify Membership", callback_data="verify_membership")])

    await message.answer(
        "👋 **Welcome!**\n\nTo use this bot, you must join our required channels first. Please join them and click **'Verify Membership'**:",
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
                        f"🎉 **Referral Bonus!** User `{user_id}` verified their membership using your link. **₹0.001** added to your balance!",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        await callback.answer("Verified successfully! ✅", show_alert=True)
        await send_main_menu(callback, "🎉 **Verification Successful!**\n\n🛒 Explore the menu below.")
    else:
        await callback.answer("❌ You haven't joined all required channels yet! Please join them first.", show_alert=True)

@dp.message(Command("wallet"))
@dp.callback_query(F.data == "wallet")
async def show_wallet(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    user = await db.get_user(user_id)
    payments = await db.get_user_payments(user_id)

    history_text = "📜 **Recent Payment History:**\n"
    if not payments:
        history_text += "No payment history yet."
    else:
        for p in payments[:5]:
            history_text += f"• ₹{p['amount']} | Status: `{p['status']}`\n"

    kb = [
        [InlineKeyboardButton(text="⭐ Add Funds via Stars", callback_data="add_funds_stars")],
        [InlineKeyboardButton(text="🎁 Redeem Promo Code", callback_data="redeem_promo_menu")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]
    ]

    text = (
        f"💰 **Your Wallet & History**\n\n"
        f"💵 Balance: **₹{user['balance']:.3f}**\n\n"
        f"{history_text}"
    )

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "add_funds_stars")
async def add_funds_stars_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(StarsTopupState.waiting_for_amount)
    await callback.message.edit_text(
        "⭐ **Add Funds via Telegram Stars**\n\n"
        f"• Minimum top-up: **{MIN_STARS} Stars**\n"
        f"• Conversion rate: `1 Star = ₹{STAR_RATE}`\n\n"
        "✍️ How many Stars do you want to add? Send a number (e.g., `50`):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="wallet")]]),
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
        f"✅ **Payment Successful!**\n\n"
        f"Successfully paid **{stars_paid} Stars ⭐**.\n"
        f"Added **₹{inr_added:.2f}** to your wallet balance!",
        parse_mode="Markdown"
    )

@dp.message(Command("referral"))
@dp.callback_query(F.data == "referral_menu")
async def referral_handler(event: types.Message | types.CallbackQuery):
    user_id = event.from_user.id
    user = await db.get_user(user_id)
    bot_user = await bot.get_me()
    ref_link = f"https://t.me/{bot_user.username}?start=ref_{user_id}"
    ref_count = user.get("referral_count", 0)

    text = (
        f"👥 **Referral Program**\n\n"
        f"Invite your friends and earn **₹0.001** for each referral when they join and verify their membership!\n\n"
        f"🔗 **Your Referral Link:**\n`{ref_link}`\n\n"
        f"📊 Total Verified Referrals: **{ref_count}**"
    )
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]

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
            "🎁 **Redeem Promo Code**\n\nPlease send your promo code below:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="back_home")]]),
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
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]
    await message.answer(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "shop")
async def show_shop(callback: types.CallbackQuery):
    categories = await db.get_categories_with_counts()
    if not categories:
        await callback.message.edit_text("❌ No categories available right now.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]))
        return

    kb = []
    for c_id, c_name, available_count in categories:
        kb.append([InlineKeyboardButton(text=f"📂 {c_name} ({available_count} Available)", callback_data=f"cat_{c_id}")])
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="back_home")])

    await callback.message.edit_text("🛍️ **Select a Category:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("cat_"))
async def show_category_items(callback: types.CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    accounts = await db.get_available_accounts(cat_id)

    if not accounts:
        await callback.message.edit_text("❌ No accounts available in this category.",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="shop")]]))
        return

    kb = [[InlineKeyboardButton(text=f"{display_name} - ₹{price}", callback_data=f"select_acc_{acc_id}")] for acc_id, display_name, price in accounts]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="shop")])

    await callback.message.edit_text("📦 **Available Accounts (Click to Buy):**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

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
        f"🛒 **Confirm Purchase**\n\n"
        f"📦 Item: {account.get('display_name', 'Account')}\n"
        f"💰 Price: `₹{price:.2f}`\n"
        f"💳 Current Balance: `₹{user_balance:.3f}`\n"
        f"💳 Balance After Purchase: `₹{after_balance:.3f}`\n\n"
        f"Do you want to proceed?"
    )

    kb = [
        [InlineKeyboardButton(text="✅ Confirm Purchase", callback_data=f"do_buy_{acc_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="shop")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("do_buy_"))
async def process_purchase(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    account = await db.get_account_by_id(acc_id)
    if not account or account.get("is_sold") == 1:
        await callback.answer("❌ This account is no longer available.", show_alert=True)
        return

    status, phone, session, price, two_step = await db.buy_account_safely(user_id, acc_id)

    if status == "success":
        asyncio.create_task(start_userbot_for_account(phone, session, bot, user_id))

        masked_phone = mask_phone_number(phone)
        masked_user = str(user_id)[:2] + "***" + str(user_id)[-3:] if len(str(user_id)) > 5 else "***"
        bot_user = await bot.get_me()
        bot_username = bot_user.username
        item_name = account.get('display_name', 'Account')

        channel_text = (
            f"🚀 **NEW ACCOUNT SOLD!**\n\n"
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

        text = (
            f"✅ **Purchase Successful!**\n\n"
            f"📱 **Number:** `{phone}`\n"
            f"🔑 **Session String:**\n`{session}`\n\n"
            f"🔐 **2-Step Password:** `{two_step if two_step else 'None'}`\n\n"
            f"👇 Click the button below to get your OTP code:"
        )

        kb = [
            [InlineKeyboardButton(text="📥 Get Code", callback_data=f"get_otp_{acc_id}")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_home")]
        ]
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

    elif status == "low_balance":
        await callback.answer("❌ Insufficient balance! Please add funds to your wallet.", show_alert=True)
    else:
        await callback.answer("❌ Sorry, this account was already sold.", show_alert=True)

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
        messages = await client.get_messages(777000, limit=1)
        if messages:
            msg_text = messages[0].message
            match = re.search(r'\b\d{4,6}\b', msg_text)
            otp_code = match.group(0) if match else msg_text
            await callback.message.answer(f"📩 **Your OTP Code:** `{otp_code}`", parse_mode="Markdown")
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

    text = "📦 **Your Orders History:**\n\n"
    if not purchases:
        text += "You haven't purchased any accounts yet."
    else:
        for idx, p in enumerate(purchases, 1):
            text += f"{idx}. Amount: `₹{p['amount']}` | Date: `{p['timestamp']}`\n"

    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]

    if isinstance(event, types.CallbackQuery):
        await event.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "support")
async def support_handler(callback: types.CallbackQuery):
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]
    await callback.message.edit_text("🆘 **Support:**\n\nFor any issues or questions, please contact the owner.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "my_profile")
async def my_profile_handler(callback: types.CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]
    await callback.message.edit_text(
        f"👤 **Your Profile**\n\n"
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
        [InlineKeyboardButton(text="📊 Detailed Analytics & Stats", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Create Promo Code", callback_data="admin_add_promo_prompt")],
        [InlineKeyboardButton(text="🔗 Supplier API Settings", callback_data="admin_supplier_menu")],
        [InlineKeyboardButton(text="🩺 Check Account Health", callback_data="admin_health_check")],
        [InlineKeyboardButton(text="📂 Manage Categories", callback_data="admin_cats")],
        [InlineKeyboardButton(text="➕ Add Account", callback_data="admin_add_acc")],
        [InlineKeyboardButton(text="🗑️ Delete Stock", callback_data="admin_del_acc_list")],
        [InlineKeyboardButton(text="📦 Sales History", callback_data="admin_sales_history")],
        [InlineKeyboardButton(text="👥 User Management", callback_data="admin_users")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="back_home")]
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    text = "⚙️ **Admin Control Panel**\n\nSelect an option below:"

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
        f"📊 **Detailed Analytics & Stock Status:**\n\n"
        f"• Available Accounts: `{stats['available']}`\n"
        f"• Sold Accounts: `{stats['sold']}`\n"
        f"• Total Registered Users: `{stats['users']}`\n"
        f"• Total Revenue Generated: `₹{stats['revenue']:.2f}`\n"
    )
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_add_promo_prompt")
async def admin_add_promo_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(CreatePromoState.waiting_for_code_name)
    await callback.message.edit_text(
        "🎁 Enter the promo code name (e.g., WELCOME):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin_panel")]])
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
    await message.answer(f"✅ Promo code `{code}` created successfully with value `₹{amount}`!", parse_mode="Markdown")

@dp.callback_query(F.data == "admin_supplier_menu")
async def admin_supplier_menu(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    config = await db.get_supplier_config()
    text = (
        f"🔗 **Supplier API Integration Settings**\n\n"
        f"• Status: `{'Active' if config['is_active'] else 'Inactive / Modular Placeholder'}`\n"
        f"• API URL: `{config['api_url'] if config['api_url'] else 'Not Set'}`\n\n"
        f"Use this section to configure external supplier auto-refill hooks when needed."
    )
    kb = [
        [InlineKeyboardButton(text="✏️ Configure API URL & Key", callback_data="config_supplier_step")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "config_supplier_step")
async def config_supplier_step(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(SupplierConfigState.waiting_for_url)
    await callback.message.edit_text("🔗 Send Supplier API Endpoint URL:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin_supplier_menu")]]))

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
    await message.answer("✅ Supplier API configuration saved successfully!")

@dp.callback_query(F.data == "admin_health_check")
async def admin_health_check(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    accounts = await db.get_all_unsold_accounts()
    total_unsold = len(accounts)
    text = (
        f"🩺 **Account Health Check Report**\n\n"
        f"• Total Unsold Accounts in Stock: `{total_unsold}`\n"
        f"• Session Validation Status: All sessions are registered in DB.\n"
    )
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_cats")
async def admin_cats_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    categories = await db.get_categories()
    kb = [[InlineKeyboardButton(text=f"✏️ Edit: {c[1]}", callback_data=f"editcat_{c[0]}")] for c in categories]
    kb.append([InlineKeyboardButton(text="➕ Add New Category", callback_data="admin_add_cat")])
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")])

    await callback.message.edit_text("📂 **Manage Categories:**\n\nClick a category to edit its name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("editcat_"))
async def edit_category_start(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    cat_id = int(callback.data.split("_")[1])
    await state.update_data(editing_cat_id=cat_id)
    await state.set_state(EditCategoryState.waiting_for_new_name)
    await callback.message.edit_text("✍ Send new category name:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin_cats")]]))

@dp.message(EditCategoryState.waiting_for_new_name)
async def save_edited_category(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    cat_id = data.get("editing_cat_id")
    new_name = message.text

    await db.update_category_name(cat_id, new_name)
    await state.clear()
    await message.answer(f"✅ Category successfully renamed to '{new_name}'!")

@dp.callback_query(F.data == "admin_users")
async def admin_users_handler(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    kb = [
        [InlineKeyboardButton(text="🚫 Ban a User", callback_data="admin_ban_user")],
        [InlineKeyboardButton(text="✅ Unban a User", callback_data="admin_unban_user")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]
    ]
    await callback.message.edit_text("👥 **User Management:**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_ban_user")
async def admin_ban_user_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BanUserState.waiting_for_user_id)
    await state.update_data(ban_action=1)
    await callback.message.edit_text("✍️ Send the **User ID** of the user you want to ban:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin_users")]]), parse_mode="Markdown")

# NOTE: the original code had no handler for admin_unban_user; added here.
@dp.callback_query(F.data == "admin_unban_user")
async def admin_unban_user_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BanUserState.waiting_for_user_id)
    await state.update_data(ban_action=0)
    await callback.message.edit_text("✍️ Send the **User ID** of the user you want to unban:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin_users")]]), parse_mode="Markdown")

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
    if action == 1:
        await message.answer(f"✅ User `{target_id}` has been successfully banned!", parse_mode="Markdown")
    else:
        await message.answer(f"✅ User `{target_id}` has been unbanned!", parse_mode="Markdown")

@dp.callback_query(F.data == "admin_sales_history")
async def admin_sales_history(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    sales = await db.get_all_sales_history(limit=10)
    text = "📦 **Recent Sales Logs:**\n\n"
    if not sales:
        text += "No sales records found."
    else:
        for s in sales:
            text += f"• User: `{s['user_id']}` | Amount: `₹{s['amount']}` | Date: `{s['timestamp']}`\n"

    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.set_state(BroadcastState.waiting_for_message)
    await callback.message.edit_text("📢 Send the message/text/photo to broadcast to all users:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="admin_panel")]]))

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
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]])
        )
        return

    kb = [[InlineKeyboardButton(text=f"❌ Delete {disp_name} (₹{price})", callback_data=f"delacc_{acc_id}")] for acc_id, disp_name, price in accounts]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")])

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
    await callback.message.edit_text("✍️ Send category name:")

@dp.message(AddCategoryState.waiting_for_name)
async def save_cat(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await db.add_category(message.text)
    await state.clear()
    await message.answer(f"✅ Category '{message.text}' added successfully!")

@dp.callback_query(F.data == "admin_add_acc")
async def admin_add_acc(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    categories = await db.get_categories()
    if not categories:
        await callback.message.edit_text("❌ Please create a category first!")
        return
    kb = [[InlineKeyboardButton(text=c[1], callback_data=f"selcat_{c[0]}")] for c in categories]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")])
    await callback.message.edit_text("📂 Select category:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("selcat_"))
async def sel_cat(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await state.update_data(category_id=int(callback.data.split("_")[1]))
    await state.set_state(AddItemState.waiting_for_display_name)
    await callback.message.edit_text("✍️ Send display name for button (e.g., 🇮🇳 India - ₹25):")

@dp.message(AddItemState.waiting_for_display_name)
async def get_display_name(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(display_name=message.text)
    await state.set_state(AddItemState.waiting_for_phone)
    await message.answer("📱 Send phone number (e.g., +91xxxxxxxxxx):")

@dp.message(AddItemState.waiting_for_phone)
async def get_phone(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(phone_number=message.text)
    await state.set_state(AddItemState.waiting_for_session)
    await message.answer("🔑 Send Telethon Session String:")

@dp.message(AddItemState.waiting_for_session)
async def get_session(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(session_string=message.text)
    await state.set_state(AddItemState.waiting_for_price)
    await message.answer("💵 Send price in ₹ (e.g., 20.0):")

@dp.message(AddItemState.waiting_for_price)
async def get_price(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        price = float(message.text)
    except ValueError:
        await message.answer("❌ Invalid price. Enter a valid number:")
        return

    data = await state.get_data()
    await db.add_account(
        data["category_id"],
        data["display_name"],
        data["phone_number"],
        data["session_string"],
        price,
        two_step=""
    )
    await state.clear()
    await message.answer("✅ Account added successfully with custom display name!")

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery):
    await send_main_menu(callback)

async def main():
    await db.init_db()
    await set_bot_commands(bot)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
