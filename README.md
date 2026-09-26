# Telegram Selling Bot — Starter

MongoDB-based Telegram selling bot starter for Railway.

## Current features

- `/start` main menu
- Wallet balance
- Add Funds
- UPI deposit request flow
- Minimum deposit ₹10
- Google Play Redeem Code deposit flow
- Screenshot submission
- Admin DM with Approve / Reject buttons
- MongoDB persistence
- My Orders placeholder
- Support placeholder
- My Profile

## Environment variables

```text
BOT_TOKEN=
OWNER_ID=
MONGO_URI=
DATABASE_NAME=selling_bot
UPI_ID=
```

## Railway

1. Push these files to GitHub.
2. Create a Railway project and deploy the GitHub repo.
3. Add the environment variables above.
4. Railway will use the Procfile worker command.

## Important

The QR image and Force Join are intentionally left for the next update. Product/Buy Now is also a placeholder because its flow has not been finalized yet.

Do not put bot tokens or MongoDB credentials into GitHub.
