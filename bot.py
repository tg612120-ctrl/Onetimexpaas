import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import BOT_TOKEN, ADMIN_ID, REQUIRED_CHANNELS, UPI_ID, CRYPTO_ADDRESS
import database as db
from userbot import start_userbot_for_account

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AddItemState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_session = State()
    waiting_for_price = State()

class AddCategoryState(StatesGroup):
    waiting_for_name = State()

# Force Join check function
async def check_user_channels(user_id: int) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception:
            return False
    return True

async def send_main_menu(message_or_callback, text="👋 Welcome to Telegram OTP Bot!\n\nChoose an option below:"):
    kb = [
        [InlineKeyboardButton(text="🛒 Buy Accounts & Get OTP", callback_data="shop")],
        [InlineKeyboardButton(text="💰 My Wallet & History", callback_data="wallet")],
    ]
    user_id = message_or_callback.from_user.id
    if user_id == ADMIN_ID:
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
    
    # Check if verified in database
    if user_data["is_verified"] == 1:
        # Double check via Telegram API if they are still in the channels
        still_member = await check_user_channels(user_id)
        if still_member:
            await send_main_menu(message, f"Welcome back, {message.from_user.first_name}! Aap already verified hain.")
            return
        else:
            # If they left any channel, reset verification status
            await db.update_verification(user_id, 0)

    # Force Join markup creation
    kb = []
    for idx, ch in enumerate(REQUIRED_CHANNELS, start=1):
        kb.append([InlineKeyboardButton(text=f"📢 Join Channel {idx}", url=f"https://t.me/{ch.lstrip('@')}")])
    kb.append([InlineKeyboardButton(text="✅ Verify Membership", callback_data="verify_membership")])
    
    await message.answer(
        "👋 **Welcome!**\n\nIs bot ko use karne ke liye aapko hamare teeno channels join karne honge. Kripya channels join karke **'Verify Membership'** par click karein:",
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
        await send_main_menu(callback, "🎉 **Verification Successful!**\n\nAapne sabhi channels join kar liye hain. Ab aap bot use kar sakte hain:")
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
        for p in payments[:5]: # Show last 5 records
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
    
    kb = [[InlineKeyboardButton(text=f"📱 {phone} - ${price}", callback_data=f"buy_{acc_id}")] for acc_id, phone, price in accounts]
    kb.append([InlineKeyboardButton(text="🔙 Back", callback_data="shop")])
    
    await callback.message.edit_text("🛍️ Available Accounts (Click to Buy):", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("buy_"))
async def buy_item(callback: types.CallbackQuery):
    acc_id = int(callback.data.split("_")[1])
    status, phone, session = await db.buy_account(callback.from_user.id, acc_id)
    
    if status == "success":
        asyncio.create_task(start_userbot_for_account(phone, session, bot, callback.from_user.id))
        await callback.message.edit_text(
            f"✅ **Purchase Successful!**\n\n"
            f"📱 Phone: `{phone}`\n"
            f"🔑 Session String: `{session}`\n\n"
            f"🤖 *OTP Listener Activated!* 777000 se aane wala OTP yahin aayega.",
            parse_mode="Markdown"
        )
    elif status == "low_balance":
        await callback.answer("❌ Insufficient balance! Wallet me balance add karein.", show_alert=True)
    else:
        await callback.answer("❌ Sorry, yeh account sold out ho chuka hai.", show_alert=True)

@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    kb = [
        [InlineKeyboardButton(text="➕ Add Category", callback_data="admin_add_cat")],
        [InlineKeyboardButton(text="➕ Add Account", callback_data="admin_add_acc")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="back_home")]
    ]
    await callback.message.edit_text("⚙️ **Admin Panel**", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(Command("givebalance"))
async def give_balance_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    args = message.text.split()
    if len(args) != 3:
        await message.answer("Usage: `/givebalance user_id amount`", parse_mode="Markdown")
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
    await callback.message.edit_text("✍️️ Send category name:")

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
    await db.add_account(data["category_id"], data["phone_number"], data["session_string"], price)
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
         
