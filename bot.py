import logging
import re
import asyncio
import sqlite3
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatJoinRequest,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    MessageHandler,
    filters,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8800949120:AAF6LLJFRSP8d7jRspqwoOD8qENOOUWHyZw"
ADMIN_ID = 5624448603

# Temporary in-memory storage
user_channels = {}
custom_messages = {}
awaiting_message_input = set()

# --- Database Setup (Data permanent rakhar jonno) ---
DB_FILE = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
        """
    )
    conn.commit()
    conn.close()

def add_user_to_db(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def get_all_users_from_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

# Database initialize
init_db()


def extract_channel_identifier(text: str) -> str:
    """Extracts username or link from formats like @channel, t.me/channel, or https://t.me/channel."""
    text = text.strip()
    match = re.search(r'(?:https?://)?(?:t\.me/|telegram\.me/)([a-zA-Z0-9_]+)', text)
    if match:
        return "@" + match.group(1)
    if text.startswith("@"):
        return text
    if re.match(r'^[a-zA-Z0-9_]{5,}$', text):
        return "@" + text
    return text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the welcome text and main menu keyboard."""
    user = update.effective_user
    add_user_to_db(user.id)

    welcome_text = (
        f"Hello {user.first_name}!\n\n"
        "Welcome to the Channel Join Request Manager Bot.\n"
        "I can automatically approve join requests for your channels and send a custom welcome message to users."
    )

    keyboard = [
        [InlineKeyboardButton("Add Channel", callback_data="add_channel")],
        [InlineKeyboardButton("Set Message", callback_data="set_message")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(
            welcome_text, reply_markup=reply_markup
        )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline keyboard button clicks."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    add_user_to_db(user_id)

    if query.data == "add_channel":
        instructions = (
            "To add your channel:\n\n"
            f"1. Add me (@{context.bot.username}) to your channel as an **Administrator**.\n"
            "2. Ensure I have the permission to **'Manage Chat'** or **'Invite Users'**.\n"
            "3. Send your Channel Username (e.g., @mychannel) or Channel Link (e.g., https://t.me/mychannel) here in the chat."
        )
        await query.message.edit_text(
            instructions,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="back_home")]]
            ),
        )
        context.user_data["awaiting_channel"] = True

    elif query.data == "set_message":
        awaiting_message_input.add(user_id)
        current_msg = custom_messages.get(
            user_id, "Thanks for joining! Welcome aboard."
        )
        text = (
            f"Your current welcome message:\n\n> {current_msg}\n\n"
            "Please send your new welcome message text now:"
        )
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back", callback_data="back_home")]]
            ),
        )

    elif query.data == "back_home":
        if user_id in awaiting_message_input:
            awaiting_message_input.remove(user_id)
        context.user_data["awaiting_channel"] = False
        await start(update, context)


async def handle_text_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text inputs for adding channels or setting custom messages."""
    user_id = update.effective_user.id
    add_user_to_db(user_id)
    text = update.message.text

    # Custom message update
    if user_id in awaiting_message_input:
        custom_messages[user_id] = text
        awaiting_message_input.remove(user_id)
        await update.message.reply_text(
            "✅ Custom message successfully updated!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("« Back to Home", callback_data="back_home")]]
            ),
        )
        return

    # Channel add
    if context.user_data.get("awaiting_channel"):
        channel_input = extract_channel_identifier(text)
        try:
            chat = await context.bot.get_chat(channel_input)
            member = await context.bot.get_chat_member(chat.id, context.bot.id)

            if member.status in ["administrator", "creator"]:
                if user_id not in user_channels:
                    user_channels[user_id] = []

                if chat.id not in user_channels[user_id]:
                    user_channels[user_id].append(chat.id)

                context.user_data["awaiting_channel"] = False
                await update.message.reply_text(
                    f"✅ Channel **{chat.title}** successfully added and configured!",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("« Back to Home", callback_data="back_home")]]
                    ),
                )
            else:
                await update.message.reply_text(
                    "❌ I am not an administrator in that channel. Please promote me to admin first and try again."
                )
        except Exception as e:
            logger.error(f"Error adding channel: {e}")
            await update.message.reply_text(
                "❌ Could not find the channel or verify permissions. Make sure the link/username is correct, I am added, and I have admin rights."
            )


async def auto_approve_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically approves join requests, saves user ID, sends welcome message, and notifies channel owner."""
    chat_join_request: ChatJoinRequest = update.chat_join_request
    chat = chat_join_request.chat
    user = chat_join_request.from_user

    # Database e save kora hocche
    add_user_to_db(user.id)

    owner_id = None
    welcome_text = "Thanks for joining! Welcome aboard."

    for o_id, channels in user_channels.items():
        if chat.id in channels:
            owner_id = o_id
            if o_id in custom_messages:
                welcome_text = custom_messages[o_id]
            break

    user_mention = f"@{user.username}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
    user_identifier = f"{user.first_name} ({user_mention}, ID: `{user.id}`)"

    approve_status = False
    try:
        await chat_join_request.approve()
        approve_status = True
        logger.info(f"Approved join request for {user.id} in channel {chat.title}")
    except Exception as e:
        logger.error(f"Failed to approve request: {e}")

    msg_status = False
    if approve_status:
        try:
            await context.bot.send_message(chat_id=user.id, text=welcome_text)
            msg_status = True
            logger.info(f"Sent welcome message to user {user.id}")
        except Exception as e:
            logger.warning(f"Could not send PM to user {user.id}: {e}")

    if owner_id:
        req_icon = "✅ Approved" if approve_status else "❌ Failed"
        msg_icon = "✅ Sent" if msg_status else "❌ Failed (User hasn't started the bot or blocked it)"

        notification_text = (
            "🔔 **New Join Request Update**\n\n"
            f"📢 **Channel:** {chat.title}\n"
            f"👤 **User:** {user_identifier}\n"
            f"📌 **Request Status:** {req_icon}\n"
            f"📩 **Welcome Message:** {msg_icon}"
        )
        try:
            await context.bot.send_message(
                chat_id=owner_id, 
                text=notification_text, 
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify owner {owner_id}: {e}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only command to check total reachable users and channels."""
    sender_id = update.effective_user.id

    if sender_id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return

    users_list = get_all_users_from_db()
    total_users_count = len(users_list)
    
    total_connected_channels = sum(len(ch_list) for ch_list in user_channels.values())

    stats_text = (
        "📊 **Bot Real-Time Statistics**\n\n"
        f"👥 **Total Reachable Users:** `{total_users_count}`\n"
        f"📢 **Total Active Channels:** `{total_connected_channels}`\n\n"
        "💡 *Eder shobar kache `/broadcast` diye message pathano sombhob.*"
    )
    await update.message.reply_text(stats_text, parse_mode="Markdown")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only broadcast system to reach all users."""
    sender_id = update.effective_user.id

    if sender_id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Please provide a message to broadcast.\n\n"
            "**Usage:** `/broadcast Your message here`",
            parse_mode="Markdown"
        )
        return

    broadcast_msg = update.message.text.split(maxsplit=1)[1]
    targets = get_all_users_from_db()
    total_users = len(targets)

    if total_users == 0:
        await update.message.reply_text("❌ No users found in database to broadcast.")
        return

    status_msg = await update.message.reply_text(f"🚀 Broadcasting to {total_users} users...")

    success = 0
    failed = 0

    for user_id in targets:
        try:
            await context.bot.send_message(chat_id=user_id, text=broadcast_msg)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await status_msg.edit_text(
        "✅ **Broadcast Completed!**\n\n"
        f"👥 **Total Targets:** {total_users}\n"
        f"🟢 **Successful:** {success}\n"
        f"🔴 **Failed/Blocked:** {failed}",
        parse_mode="Markdown"
    )


def main():
    """Start the bot."""
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(ChatJoinRequestHandler(auto_approve_requests))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_inputs)
    )

    print("Bot is up and running...")
    application.run_polling()


if __name__ == "__main__":
    main()
