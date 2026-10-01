import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_TOKEN, OWNER_ID, REQUIRED_CHANNELS, UPI_ID, CRYPTO_ADDRESS
import database as db
from userbot import start_userbot_for_account, active_clients

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AddItemState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_session = State()
    waiting_for_price = State()

class AddCategoryState(StatesGroup):
    waiting_for_name = State()

async def check_user_channels(user_id: int) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True

async def send_main_menu(message_or_callback, text="🛒 Products dekhne aur wallet manage karne ke liye menu explore karein."):
    kb = [
        [
            InlineKeyboardButton(text="🛒 Buy Now", callback_data="shop"),
            InlineKeyboardButton(text="💳 Add Funds", callback_data="wallet")
        ],
        [
            InlineKeyboardButton(text="📦 My Orders", callback_data="my_orders")
        ],
        [
            InlineKeyboardButton(text="🆘 Support", callback_data="support"),
            InlineKeyboardButton(text="👤 My Profile", callback_data="my_profile")
        ]
    ]
    user_id = message_or_callback.from_user.id
    if user_id == OWNER_ID:
        kb.append([InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="admin_panel")])
    
    markup = InlineKeyboardMarkup(inline_keyboard=kb)
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=markup)
    else:
        await message_or_callback.answer(text, reply_markup=markup)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_data = await db.get_user(user_id)
    
    if user_data["is_verified"] == 1:
        still_member = await check_user_channels(user_id)
        if still_member:
            await send_main_menu(message, f"Welcome back, {message.from_user.first_name}!\n\n🛒 Products dekhne aur wallet manage karne ke liye menu explore karein.")
            return
        else:
            await db.update_verification(user_id, 0)

    kb = []
    for idx, ch in enumerate(REQUIRED_CHANNELS, start=1):
        kb.append([InlineKeyboardButton(text=f"📢 Join Channel {idx}", url=f"https://t.me/{ch.lstrip('@')}")])
    kb.append([InlineKeyboardButton(text="✅ Verify Membership", callback_data="verify_membership")])
    
    await message.answer(
        "👋 **Welcome!**\n\nIs bot ko use karne ke liye aapko hamare channels join karne honge. Kripya channels join karke **'Verify Membership'** par click karein:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "verify_membership")
async def verify_membership_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    joined = await check_user_channels(user_id)
    
    if joined:
        await db.update_verification(user_id, 1)
        await callback.answer("Verified successfully! ✅", show_alert=True)
        await send_main_menu(callback, "🎉 **Verification Successful!**\n\n🛒 Products dekhne aur wallet manage karne ke liye menu explore karein.")
    else:
        await callback.answer("❌ Aapne abhi tak saare channels join nahi kiye hain! Kripya join karein.", show_alert=True)

@dp.callback_query(F.data == "wallet")
async def show_wallet(callback: types.CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    payments = await db.get_user_payments(callback.from_user.id)
    
    history_text = "📜 **Recent Payments History:**\n"
    if not payments:
        history_text += "No payment history yet."
    else:
        for p in payments[:5]:
            history_text += f"• {p['amount']} {p['currency']} | Status: `{p['status']}`\n"

    kb = [
        [InlineKeyboardButton(text="💳 Add Balance Instructions", callback_data="add_bal_info")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]
    ]
    await callback.message.edit_text(
        f"💰 **Your Wallet & History**\n\n"
        f"💵 Balance: **${user['balance']:.2f}**\n\n"
        f"{history_text}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@dp.callback_query(F.data == "add_bal_info")
async def add_bal_info(callback: types.CallbackQuery):
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="wallet")]]
    await callback.message.edit_text(
        f"💳 **Add Balance Details:**\n\n"
        f"• **UPI ID:** `{UPI_ID}`\n"
        f"• **Crypto (USDT):** `{CRYPTO_ADDRESS}`\n\n"
        f"Payment karke screenshot aur apna User ID (`{callback.from_user.id}`) admin ko bhejiye.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@dp.callback_query(F.data == "shop")
async def show_shop(callback: types.CallbackQuery):
    categories = await db.get_categories()
    if not categories:
        await callback.message.edit_text("❌ No categories available right now.", 
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]))
        return
    
    kb = [[InlineKeyboardButton(text=c[1], callback_data=f"cat_{c[0]}")] for c in categories]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="back_home")])
    
    await callback.message.edit_text("📂 Select a category:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("cat_"))
async def show_category_items(callback: types.CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    accounts = await db.get_available_accounts(cat_id)
    
    if not accounts:
        await callback.message.edit_text("❌ No accounts available in this category.", 
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="shop")]]))
        return
    
    kb = [[InlineKeyboardButton(text=f"📱 {phone} - ${price}", callback_data=f"select_acc_{acc_id}")] for acc_id, phone, price in accounts]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="shop")])
    
    await callback.message.edit_text("🛍️ Available Accounts (Click to Buy):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Confirm Purchase Screen (Direct Buy Nahi Hoga)
@dp.callback_query(F.data.startswith("select_acc_"))
async def show_purchase_confirmation(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    account = await db.get_account_by_id(acc_id)
    
    if not account:
        await callback.answer("❌ Yeh account ab available nahi hai.", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    user_balance = user['balance']
    price = account['price']
    after_balance = user_balance - price

    text = (
        f"🛒 **Confirm Purchase**\n\n"
        f"🌍 Country: 🇮🇳 India\n"
        f"💰 Price: `${price:.2f}`\n"
        f"💳 Current balance: `${user_balance:.2f}`\n"
        f"💳 After purchase: `${after_balance:.2f}`\n\n"
        f"Proceed?"
    )

    kb = [
        [InlineKeyboardButton(text="✅ Confirm Purchase", callback_data=f"do_buy_{acc_id}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="shop")]
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")

# Confirm dabane par Balance Katega aur Details + Get Code Button aayega
@dp.callback_query(F.data.startswith("do_buy_"))
async def process_purchase(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    status, phone, session, price, two_step = await db.buy_account_safely(user_id, acc_id)
    
    if status == "success":
        asyncio.create_task(start_userbot_for_account(phone, session, bot, user_id))
        
        text = (
            f"✅ **Purchase Successful!**\n\n"
            f"📱 **Number:** `{phone}`\n"
            f"🌍 **Country:** India\n"
            f"🔑 **Session String:**\n`{session}`\n\n"
            f"🔐 **2-Step Password:** `{two_step if two_step else 'None'}`\n\n"
            f"👇 OTP lene ke liye neeche diye gaye button par click karein:"
        )
        
        kb = [
            [InlineKeyboardButton(text="📥 Get Code", callback_data=f"get_otp_{acc_id}")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_home")]
        ]
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="Markdown")
    elif status == "low_balance":
        await callback.answer("❌ Insufficient balance! Wallet me balance add karein.", show_alert=True)
    else:
        await callback.answer("❌ Sorry, yeh account pehle hi bik chuka hai.", show_alert=True)

# "Get Code" button dabane par hi OTP deliver hoga
@dp.callback_query(F.data.startswith("get_otp_"))
async def get_otp_handler(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[2])
    account = await db.get_account_by_id(acc_id)
    
    if not account:
        await callback.answer("❌ Account details not found.", show_alert=True)
        return
        
    phone = account.get('phone_number')
    client = active_clients.get(phone)
    
    if not client:
        asyncio.create_task(start_userbot_for_account(phone, account['session_string'], bot, callback.from_user.id))
        await callback.answer("⏳ Userbot initialize ho raha hai, 5 seconds baad dobara 'Get Code' dabayein.", show_alert=True)
        return

    try:
        messages = await client.get_messages(777000, limit=1)
        if messages:
            msg_text = messages[0].message
            import re
            match = re.search(r'\b\d{4,6}\b', msg_text)
            otp_code = match.group(0) if match else msg_text
            await callback.message.answer(f"📩 **Aapka OTP Code:** `{otp_code}`", parse_mode="Markdown")
            await callback.answer("OTP sent successfully! ✅")
        else:
            await callback.answer("⚠️ Abhi tak koi OTP nahi aaya hai. Thodi der baad try karein.", show_alert=True)
    except Exception as e:
        await callback.answer(f"⚠️ Error fetching OTP: {e}", show_alert=True)

@dp.callback_query(F.data == "my_orders")
async def my_orders_handler(callback: types.CallbackQuery):
    payments = await db.get_user_payments(callback.from_user.id)
    purchases = [p for p in payments if p['status'] == 'SPEND_BUY_ACCOUNT']
    
    text = "📦 **Your Orders History:**\n\n"
    if not purchases:
        text += "Aapne abhi tak koi account nahi kharida hai."
    else:
        for idx, p in enumerate(purchases, 1):
            text += f"{idx}. Amount: `${p['amount']}` | Date: `{p['timestamp']}`\n"

    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "support")
async def support_handler(callback: types.CallbackQuery):
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]
    await callback.message.edit_text("🆘 **Support:**\n\nKisi bhi samasya ya balance add karwane ke liye owner se sampark karein.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "my_profile")
async def my_profile_handler(callback: types.CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    kb = [[InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]]
    await callback.message.edit_text(
        f"👤 **Your Profile**\n\n"
        f"🆔 User ID: `{callback.from_user.id}`\n"
        f"💵 Balance: **${user['balance']:.2f}**\n"
        f"✅ Verified: **{'Yes' if user['is_verified'] == 1 else 'No'}**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    kb = [
        [InlineKeyboardButton(text="➕ Add Category", callback_data="admin_add_cat")],
        [InlineKeyboardButton(text="➕ Add Account", callback_data="admin_add_acc")],
        [InlineKeyboardButton(text="🗑️ Delete Account", callback_data="admin_del_acc_list")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]
    ]
    await callback.message.edit_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Delete Account Handlers for Admin
@dp.callback_query(F.data == "admin_del_acc_list")
async def admin_del_acc_list(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    
    accounts = await db.get_all_unsold_accounts()
    if not accounts:
        await callback.message.edit_text(
            "❌ Delete karne ke liye koi account available nahi hai.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]])
        )
        return

    kb = [[InlineKeyboardButton(text=f"❌ Delete {phone} (${price})", callback_data=f"delacc_{acc_id}")] for acc_id, phone, price in accounts]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")])
    
    await callback.message.edit_text("🗑️ **Delete Account:**\n\nJis account ko delete karna hai us par click karein:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("delacc_"))
async def delete_account_action(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    
    acc_id = int(callback.data.split("_")[1])
    await db.delete_account(acc_id)
    
    await callback.answer("✅ Account successfully delete kar diya gaya hai!", show_alert=True)
    await admin_del_acc_list(callback)

@dp.message(Command("add"))
async def give_balance_cmd(message: types.Message):
    if message.from_user.id != OWNER_ID:
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Usage: `/add user_id amount`", parse_mode="Markdown")
        return
    try:
        target_user_id = int(args[1])
        amount = float(args[2])
        await db.update_balance(target_user_id, amount)
        await db.save_payment_record(target_user_id, amount, "USD", "ADMIN_DEPOSIT")
        await message.answer(f"✅ Added ${amount} to user `{target_user_id}`!", parse_mode="Markdown")
        await bot.send_message(target_user_id, f"🎉 **Wallet Updated!** Admin added **${amount}** to your balance.", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Error: {e}")

@dp.callback_query(F.data == "admin_add_cat")
async def admin_add_cat(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddCategoryState.waiting_for_name)
    await callback.message.edit_text("✍ Send category name:")

@dp.message(AddCategoryState.waiting_for_name)
async def save_cat(message: types.Message, state: FSMContext):
    await db.add_category(message.text)
    await state.clear()
    await message.answer(f"✅ Category '{message.text}' added!")

@dp.callback_query(F.data == "admin_add_acc")
async def admin_add_acc(callback: types.CallbackQuery, state: FSMContext):
    categories = await db.get_categories()
    if not categories:
        await callback.message.edit_text("❌ Create category first!")
        return
    kb = [[InlineKeyboardButton(text=c[1], callback_data=f"selcat_{c[0]}")] for c in categories]
    await callback.message.edit_text("📂 Select category:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("selcat_"))
async def sel_cat(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(category_id=int(callback.data.split("_")[1]))
    await state.set_state(AddItemState.waiting_for_phone)
    await callback.message.edit_text("📱 Send phone number (e.g., +91xxxxxxxxxx):")

@dp.message(AddItemState.waiting_for_phone)
async def get_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone_number=message.text)
    await state.set_state(AddItemState.waiting_for_session)
    await message.answer("🔑 Send Telethon Session String:")

@dp.message(AddItemState.waiting_for_session)
async def get_session(message: types.Message, state: FSMContext):
    await state.update_data(session_string=message.text)
    await state.set_state(AddItemState.waiting_for_price)
    await message.answer("💵 Send price (e.g., 5.0):")

@dp.message(AddItemState.waiting_for_price)
async def get_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text)
    except ValueError:
        await message.answer("❌ Invalid price. Enter a number:")
        return
    
    data = await state.get_data()
    await db.add_account(data["category_id"], data["phone_number"], data["session_string"], price, two_step="")
    await state.clear()
    await message.answer("✅ Account added successfully!")

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery):
    await send_main_menu(callback)

async def main():
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
