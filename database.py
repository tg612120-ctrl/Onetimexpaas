from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client["telegram_otp_bot"]

categories_col = db["categories"]
accounts_col = db["accounts"]
users_col = db["users"]
payments_col = db["payments"]
counters_col = db["counters"]

async def get_next_sequence(name: str) -> int:
    counter = await counters_col.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return counter["seq"]

async def init_db():
    await categories_col.create_index("name", unique=True)
    await accounts_col.create_index("account_id", unique=True)
    await users_col.create_index("user_id", unique=True)

# User / Wallet & Verification functions
async def get_user(user_id: int):
    user = await users_col.find_one({"user_id": user_id})
    if not user:
        user = {"user_id": user_id, "balance": 0.0, "is_verified": 0}
        await users_col.insert_one(user)
    return user

async def update_verification(user_id: int, status: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"is_verified": status}},
        upsert=True
    )

async def update_balance(user_id: int, amount: float):
    await users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"balance": amount}},
        upsert=True
    )

# Payment History functions
async def save_payment_record(user_id: int, amount: float, currency: str, status: str):
    await payments_col.insert_one({
        "user_id": user_id,
        "amount": amount,
        "currency": currency,
        "status": status,
        "timestamp": datetime.utcnow()
    })

async def get_user_payments(user_id: int):
    cursor = payments_col.find({"user_id": user_id}).sort("timestamp", -1)
    return await cursor.to_list(length=50)

# Categories & Accounts functions
async def get_categories():
    cursor = categories_col.find({})
    return [(doc["category_id"], doc["name"]) async for doc in cursor]

async def add_category(name: str):
    existing = await categories_col.find_one({"name": name})
    if not existing:
        cat_id = await get_next_sequence("category_id")
        await categories_col.insert_one({"category_id": cat_id, "name": name})

async def add_account(category_id: int, phone_number: str, session_string: str, price: float):
    acc_id = await get_next_sequence("account_id")
    await accounts_col.insert_one({
        "account_id": acc_id,
        "category_id": category_id,
        "phone_number": phone_number,
        "session_string": session_string,
        "price": price,
        "is_sold": 0
    })

async def get_available_accounts(category_id: int):
    cursor = accounts_col.find({"category_id": category_id, "is_sold": 0})
    return [(doc["account_id"], doc["phone_number"], doc["price"]) async for doc in cursor]

async def buy_account(user_id: int, account_id: int):
    user = await get_user(user_id)
    account = await accounts_col.find_one({"account_id": account_id, "is_sold": 0})
    
    if not account:
        return "not_found", None, None
    
    price = account["price"]
    if user["balance"] < price:
        return "low_balance", None, None
    
    await update_balance(user_id, -price)
    await accounts_col.update_one({"account_id": account_id}, {"$set": {"is_sold": 1}})
    
    # Save purchase record in payments history as well
    await save_payment_record(user_id, price, "USD", "SPEND_BUY_ACCOUNT")
    
    return "success", account["phone_number"], account["session_string"]
  
