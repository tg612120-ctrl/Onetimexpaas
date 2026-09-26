import os
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)
from pymongo import MongoClient

BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
DATABASE_NAME = os.getenv("DATABASE_NAME", "selling_bot")
OWNER_ID = int(os.environ["OWNER_ID"])

MIN_DEPOSIT = 10
UPI_ID = os.getenv("UPI_ID", "your-upi-id@upi")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo = MongoClient(MONGO_URI)
db = mongo[DATABASE_NAME]
users = db.users
deposits = db.deposits

AMOUNT, UPI_PROOF, REDEEM_PROOF, REDEEM_SCREENSHOT = range(4)

def now():
    return datetime.now(timezone.utc)

def user_doc(tg_user):
    return {
        "user_id": tg_user.id,
        "name": tg_user.full_name,
        "username": tg_user.username,
        "balance": 0,
        "created_at": now(),
        "updated_at": now(),
    }

def ensure_user(tg_user):
    users.update_one(
        {"user_id": tg_user.id},
        {"$set": {"name": tg_user.full_name, "username": tg_user.username, "updated_at": now()},
         "$setOnInsert": {"balance": 0, "created_at": now()}},
        upsert=True,
    )

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Buy Now", callback_data="buy"),
         InlineKeyboardButton("💳 Add Funds", callback_data="add_funds")],
        [InlineKeyboardButton("📦 My Orders", callback_data="orders")],
        [InlineKeyboardButton("🆘 Support", callback_data="support"),
         InlineKeyboardButton("👤 My Profile", callback_data="profile")],
    ])

def add_funds_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 UPI (PhonePe/GPay/Paytm)", callback_data="pay_upi")],
        [InlineKeyboardButton("🎁 Google Play Redeem Code", callback_data="pay_redeem")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")],
    ])

def back_home():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="home")]])

FORCE_JOIN_CHATS = [
    ("🇮🇳 Support Channel", "@zyXzo", "https://t.me/zyXzo"),
    ("📋 Logs Channel", "@arcfluxx", "https://t.me/arcfluxx"),
    ("👥 Support Group", "@genzportals", "https://t.me/genzportals"),
]


def force_join_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(name, url=url)]
        for name, _, url in FORCE_JOIN_CHATS
    ] + [
        [InlineKeyboardButton("✅ I HAVE JOINED ALL", callback_data="verify_join")]
    ])


async def check_force_join(bot, user_id):
    for _, chat_id, _ in FORCE_JOIN_CHATS:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked"):
                return False
            if member.status == "restricted" and getattr(member, "is_member", False) is False:
                return False
        except Exception as exc:
            logger.warning("Force-join check failed for %s: %s", chat_id, exc)
            return False
    return True


async def send_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒 JOIN TO CONTINUE\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Please join all the channels and the support group below to use this bot.\n\n"
        "Once you have joined all three, tap Verify to continue.\n\n"
        "You need to be a member of all 3 for verification to pass."
    )
    return await update.message.reply_text(text, reply_markup=force_join_menu())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)
    await send_force_join(update, context)


async def verify_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query

    if not await check_force_join(context.bot, q.from_user.id):
        await q.answer(
            "❌ You have not joined all required channels/groups yet.",
            show_alert=True,
        )
        return

    try:
        await q.message.delete()
    except Exception:
        pass

    await context.bot.send_message(
        chat_id=q.from_user.id,
        text=(
            f"👋 Welcome, {q.from_user.first_name}!\n\n"
            "🛒 Browse products and manage your wallet from the menu below."
        ),
        reply_markup=main_menu(),
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ensure_user(q.from_user)

    if q.data == "verify_join":
        await verify_join_callback(update, context)
        return

    if q.data == "home":
        await q.edit_message_text(
            f"👋 Welcome, {q.from_user.first_name}!\n\n"
            "🛒 Browse products and manage your wallet from the menu below.",
            reply_markup=main_menu()
        )

    elif q.data == "add_funds":
        balance = users.find_one({"user_id": q.from_user.id}).get("balance", 0)
        await q.edit_message_text(
            f"🏦 ADD FUNDS\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Wallet Balance: ₹{balance}\n\n"
            "👇 Select Payment Method:",
            reply_markup=add_funds_menu()
        )

    elif q.data in ("pay_upi", "pay_redeem"):
        method = "UPI" if q.data == "pay_upi" else "Google Play Redeem Code"
        context.user_data["deposit_method"] = method
        await q.edit_message_text(
            "💰 ENTER AMOUNT\n━━━━━━━━━━━━━━━━━━\n\n"
            "Send the amount you want to deposit.\n\n"
            "Example: ₹50, ₹100, ₹500\n\n"
            "⚠️ Minimum deposit: ₹10",
            reply_markup=back_home()
        )
        return AMOUNT

    elif q.data == "buy":
        await q.edit_message_text(
            "🛒 BUY NOW\n\nProduct/inventory interface will be added next.",
            reply_markup=back_home()
        )

    elif q.data == "orders":
        await q.edit_message_text("📦 MY ORDERS\n\nNo orders yet.", reply_markup=back_home())

    elif q.data == "support":
        await q.edit_message_text(
            "🆘 SUPPORT\n\nPlease contact the administrator for assistance.",
            reply_markup=back_home()
        )

    elif q.data == "profile":
        u = users.find_one({"user_id": q.from_user.id}) or {}
        await q.edit_message_text(
            f"👤 MY PROFILE\n━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 User ID: {q.from_user.id}\n"
            f"👤 Name: {q.from_user.full_name}\n"
            f"💰 Balance: ₹{u.get('balance', 0)}",
            reply_markup=back_home()
        )

    return ConversationHandler.END

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    try:
        amount = int(text)
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid whole amount.", reply_markup=back_home())
        return AMOUNT

    if amount < MIN_DEPOSIT:
        await update.message.reply_text(
            f"❌ Invalid Amount\n\nMinimum deposit amount is ₹{MIN_DEPOSIT}.\n\nPlease enter an amount of ₹{MIN_DEPOSIT} or more."
        )
        return AMOUNT

    context.user_data["deposit_amount"] = amount
    method = context.user_data.get("deposit_method")

    if method == "UPI":
        await update.message.reply_text(
            f"💳 UPI PAYMENT - ₹{amount}\n━━━━━━━━━━━━━━━━━━\n\n"
            "📷 QR CODE: Add your UPI QR image in the next bot update.\n\n"
            f"UPI ID: {UPI_ID}\n\n"
            "1️⃣ Pay the exact amount.\n"
            "2️⃣ After successful payment, press the button below.\n"
            "3️⃣ Then send a clear payment screenshot.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I HAVE PAID", callback_data="upi_paid")],
                [InlineKeyboardButton("⬅️ Cancel", callback_data="home")]
            ])
        )
    else:
        await update.message.reply_text(
            f"🎁 GOOGLE PLAY REDEEM CODE\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Deposit Amount: ₹{amount}\n\n"
            "📸 First send your redeem code.\n"
            "Then send a screenshot/proof of purchase.\n\n"
            "⚠️ NOTE:\n"
            "• Submit codes only from legitimate/authorized sources.\n"
            "• The code must be unused and valid.\n"
            "• Keep your purchase receipt/proof available for verification.\n\n"
            "After sending the code and screenshot, your request will be reviewed manually.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="home")]])
        )
    return UPI_PROOF if method == "UPI" else REDEEM_PROOF

async def paid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data != "upi_paid":
        return ConversationHandler.END

    await q.message.delete()
    await q.message.chat.send_message(
        "📸 Now send your payment screenshot.\n\n"
        "Please send a clear screenshot of your successful payment."
    )
    context.user_data["waiting_proof"] = True
    return UPI_PROOF

async def redeem_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = (update.message.text or "").strip()
    if not code:
        await update.message.reply_text("❌ Please send a valid redeem code.")
        return REDEEM_PROOF

    context.user_data["redeem_code"] = code
    await update.message.reply_text(
        "✅ Redeem code received.\n\n"
        "📸 Now send your purchase screenshot.\n"
        "Please send a clear screenshot of the purchase/proof."
    )
    return REDEEM_SCREENSHOT

async def proof_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("deposit_method") == "Google Play Redeem Code":
        waiting = context.user_data.get("redeem_code")
    else:
        waiting = context.user_data.get("waiting_proof")

    if not waiting:
        return ConversationHandler.END

    method = context.user_data.get("deposit_method")
    amount = context.user_data.get("deposit_amount")

    if not update.message.photo:
        await update.message.reply_text("❌ Please send the screenshot as an image.")
        return UPI_PROOF if method == "UPI" else REDEEM_SCREENSHOT

    file_id = update.message.photo[-1].file_id
    deposit = {
        "user_id": update.effective_user.id,
        "name": update.effective_user.full_name,
        "username": update.effective_user.username,
        "method": method,
        "amount": amount,
        "proof_file_id": file_id,
        "status": "pending",
        "created_at": now(),
    }
    if method == "Google Play Redeem Code":
        deposit["redeem_code"] = context.user_data.get("redeem_code")
    result = deposits.insert_one(deposit)

    await update.message.reply_text(
        "✅ Submitted!\n\n"
        "Your payment screenshot has been submitted successfully.\n\n"
        "⏳ Please wait for admin approval."
    )

    caption = (
        "💳 NEW DEPOSIT REQUEST\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {update.effective_user.full_name}\n"
        f"🆔 User ID: {update.effective_user.id}\n"
        f"💰 Amount: ₹{amount}\n"
        f"📌 Method: {method}\n"
        + (f"🎁 Redeem Code: {context.user_data.get('redeem_code')}\n" if method == "Google Play Redeem Code" else "")
        + f"🆔 Request: {result.inserted_id}\n\n"
        "Review the attached proof and approve/reject manually."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ APPROVE", callback_data=f"approve:{result.inserted_id}"),
        InlineKeyboardButton("❌ REJECT", callback_data=f"reject:{result.inserted_id}")
    ]])
    await context.bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=keyboard)

    context.user_data.clear()
    return ConversationHandler.END

async def admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != OWNER_ID:
        await q.answer("Not authorized.", show_alert=True)
        return

    action, raw_id = q.data.split(":", 1)
    from bson import ObjectId
    try:
        oid = ObjectId(raw_id)
    except Exception:
        return

    dep = deposits.find_one({"_id": oid})
    if not dep or dep["status"] != "pending":
        await q.answer("Already processed or not found.", show_alert=True)
        return

    if action == "approve":
        users.update_one({"user_id": dep["user_id"]}, {"$inc": {"balance": dep["amount"]}})
        deposits.update_one({"_id": oid}, {"$set": {"status": "approved", "approved_at": now()}})
        await context.bot.send_message(
            dep["user_id"],
            f"✅ Payment Approved\n\n₹{dep['amount']} has been added to your wallet."
        )
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n✅ APPROVED")
    else:
        deposits.update_one({"_id": oid}, {"$set": {"status": "rejected", "rejected_at": now()}})
        await context.bot.send_message(
            dep["user_id"],
            "❌ Payment Rejected\n\nYour deposit request was not approved."
        )
        await q.edit_message_caption(caption=(q.message.caption or "") + "\n\n❌ REJECTED")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(callbacks, pattern=r"^(pay_upi|pay_redeem)$"),
        ],
        states={
            AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received),
            ],
            UPI_PROOF: [
                CallbackQueryHandler(paid_callback, pattern=r"^upi_paid$"),
                MessageHandler(filters.PHOTO, proof_received),
            ],
            REDEEM_PROOF: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_code_received),
            ],
            REDEEM_SCREENSHOT: [
                MessageHandler(filters.PHOTO, proof_received),
            ],
        },
        fallbacks=[CallbackQueryHandler(callbacks)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_decision, pattern=r"^(approve|reject):"))
    app.add_handler(CallbackQueryHandler(callbacks))

    logger.info("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
