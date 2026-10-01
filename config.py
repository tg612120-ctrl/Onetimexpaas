import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 0))
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")
MONGO_URI = os.getenv("MONGO_URI")
UPI_ID = os.getenv("UPI_ID", "Not Set")
CRYPTO_ADDRESS = os.getenv("CRYPTO_ADDRESS", "Not Set")

# Yahan apne 3 required channels ke usernames daalein
REQUIRED_CHANNELS = ["@genzportals", "@zyXzo", "@arcfluxx"]

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing!")
if not MONGO_URI:
    raise ValueError("MONGO_URI is missing!")
