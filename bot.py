import logging
import os
import json
from typing import Union

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
ASK_NAME, ASK_QUESTIONS = range(2)

# Ephemeral file persistence (OK on Render, resets on restart/redeploy)
STATE_FILE = "players.json"
players = {}  # user_id(str) -> {"name": str, "score": int, "q_index": int}

# Questions: (question, [options...], correct_index)
questions = [
    ("What is the capital of France?", ["Berlin", "Madrid", "Paris", "Rome"], 2),
    ("Who wrote 'Hamlet'?", ["Tolstoy", "Shakespeare", "Homer", "Goethe"], 1),
    ("What is 5 * 6?", ["11", "30", "56", "60"], 1),
    ("Which planet is the Red Planet?", ["Venus", "Mars", "Jupiter", "Mercury"], 1),
    ("What is the largest mammal?", ["Elephant", "Blue Whale", "Giraffe", "Hippo"], 1),
]


# ---------- Persistence helpers ----------
def load_state():
    global players
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                players = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            players = {}
    else:
        players = {}


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(players, f)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


# ---------- Utility helpers ----------
def get_user_id(obj: Union[Update, "CallbackQuery"]) -> str:
    # obj is either Update or CallbackQuery
    if hasattr(obj, "from_user"):
        return str(obj.from_user.id)
    # Update.effective_user exists when coming from a message-based handler
    return str(obj.effective_user.id)


def reply_text_for(update_or_query):
    # Uniform way to send new messages (not edits)
    if hasattr(update_or_query, "message") and update_or_query.message:
        return update_or_query.message.reply_text
    # For CallbackQuery: message is available via .message
    return update_or_query.message.reply_text


# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Allow start from any state: reset user's record
    user_id = str(update.effective_user.id)
    # Reset session for user
    players[user_id] = {"name": "", "score": 0, "q_index": 0}
    save_state()

    await update.message.reply_text("Welcome! Please enter your name:")
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text("Please enter a non-empty name:")
        return ASK_NAME

    players[user_id] = {"name": name, "score": 0, "q_index": 0}
    save_state()

    await update.message.reply_text(f"Hi {name}! Let's start the quiz 🎉")
    return await ask_question(update, context)


async def ask_question(update_or_query: Union[Update, "CallbackQuery"], context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update_or_query)
    # If user has no session (e.g., pressed button after restart), bootstrap
    if user_id not in players:
        players[user_id] = {"name": "", "score": 0, "q_index": 0}
        save_state()

    player = players[user_id]
    idx = player["q_index"]

    if idx < len(questions):
        q, options, _ = questions[idx]
        keyboard = [
            [InlineKeyboardButton(opt, callback_data=str(i))] for i, opt in enumerate(options)
        ]
        send = reply_text_for(update_or_query)
        await send(q, reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_QUESTIONS
    else:
        return await finish_quiz(update_or_query, context)


async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # acknowledge quickly to prevent "Loading..." spinner
    user_id = str(query.from_user.id)

    # Guard: if state missing (e.g., bot restarted), reinitialize
    if user_id not in players:
        players[user_id] = {"name": "", "score": 0, "q_index": 0}

    player = players[user_id]
    idx = player["q_index"]

    # Ignore taps if index already advanced (prevents multi-taps race)
    if idx >= len(questions):
        await query.edit_message_text("Quiz already finished. Type /start to retry.")
        return ConversationHandler.END

    # Evaluate answer
    try:
        chosen = int(query.data)
    except ValueError:
        chosen = -1

    _, options, correct_idx = questions[idx]
    correct_text = options[correct_idx]

    if chosen == correct_idx:
        player["score"] += 1
        await query.edit_message_text("✅ Correct!")
    else:
        await query.edit_message_text(f"❌ Wrong! Correct: {correct_text}")

    # Advance
    player["q_index"] += 1
    save_state()

    # Ask next or finish
    return await ask_question(query, context)


async def finish_quiz(update_or_query: Union[Update, "CallbackQuery"], context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update_or_query)
    player = players.get(user_id, {"name": "You", "score": 0, "q_index": 0})

    # Build leaderboard
    leaderboard = sorted(players.values(), key=lambda x: x["score"], reverse=True)
    lines = ["🏆 Leaderboard:"]
    for i, p in enumerate(leaderboard[:10], start=1):
        lines.append(f"{i}. {p['name']} - {p['score']}")

    # Show user's rank if outside top 10
    try:
        user_rank = leaderboard.index(player) + 1
    except ValueError:
        # Happens if player missing due to restart; compute rank by score match
        user_rank = next((i + 1 for i, p in enumerate(leaderboard) if p is player), len(leaderboard))

    if user_rank > 10:
        lines.append("...")
        lines.append(f"{user_rank}. {player['name']} - {player['score']}")

    msg = f"Quiz finished! 🎉\nYour score: {player['score']}\n\n" + "\n".join(lines)

    send = reply_text_for(update_or_query)
    await send(msg)

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Cancel current conversation for the user
    user_id = str(update.effective_user.id)
    # Do not wipe historical score unless reset requested; just end flow
    await update.message.reply_text("Quiz cancelled. Use /start to begin again.")
    return ConversationHandler.END


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Hard reset the user's session and score
    user_id = str(update.effective_user.id)
    if user_id in players:
        players.pop(user_id, None)
        save_state()
    await update.message.reply_text("Your session is reset. Type /start to begin.")
    return ConversationHandler.END


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show leaderboard at any time
    if not players:
        await update.message.reply_text("No scores yet. Type /start to play!")
        return

    leaderboard = sorted(players.values(), key=lambda x: x["score"], reverse=True)
    lines = ["🏆 Leaderboard:"]
    for i, p in enumerate(leaderboard[:10], start=1):
        lines.append(f"{i}. {p['name']} - {p['score']}")
    await update.message.reply_text("\n".join(lines))


# ---------- Global error handler ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

    try:
        if isinstance(update, Update):
            if update.message:
                await update.message.reply_text(
                    "⚠️ Something went wrong. Please type /start to restart the quiz."
                )
            elif update.callback_query:
                await context.bot.send_message(
                    chat_id=update.callback_query.message.chat.id,
                    text="⚠️ Something went wrong. Please type /start to restart the quiz."
                )
    except Exception as e:
        logger.error(f"Failed to notify user about error: {e}")



# ---------- Main ----------
def main():
    load_state()

    bot_token = os.getenv("bot_token")
    if not bot_token:
        raise RuntimeError("bot_token env variable is required.")

    app = Application.builder().token(bot_token).build()

    # Conversation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_QUESTIONS: [CallbackQueryHandler(handle_answer)],
        },
        # Allow /start and /cancel from any state
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        allow_reentry=True,  # user can /start again even after END
    )

    # Commands outside conversation
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))

    # Error handler
    app.add_error_handler(error_handler)

    # Render webhook setup
    port = int(os.environ.get("PORT", 8443))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=bot_token,
        webhook_url=f"https://telequizbot.onrender.com/{bot_token}",
    )


if __name__ == "__main__":
    main()

