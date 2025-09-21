import logging
import os
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

ASK_NAME, ASK_QUESTIONS = range(2)

# In-memory + file persistence
STATE_FILE = "players.json"
players = {}

# Example questions (Q, options, correct_index)
questions = [
    ("What is the capital of France?", ["Berlin", "Madrid", "Paris", "Rome"], 2),
    ("Who wrote 'Hamlet'?", ["Tolstoy", "Shakespeare", "Homer", "Goethe"], 1),
    ("What is 5 * 6?", ["11", "30", "56", "60"], 1),
    ("Which planet is the Red Planet?", ["Venus", "Mars", "Jupiter", "Mercury"], 1),
    ("Largest mammal?", ["Elephant", "Blue Whale", "Giraffe", "Hippo"], 1),
]

def load_state():
    global players
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            players = json.load(f)

def save_state():
    with open(STATE_FILE, "w") as f:
        json.dump(players, f)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Please enter your name:")
    return ASK_NAME

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    name = update.message.text.strip()
    players[user_id] = {"name": name, "score": 0, "q_index": 0}
    save_state()
    await update.message.reply_text(f"Hi {name}! Let's start the quiz 🎉")
    return await ask_question(update, context)

async def ask_question(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    """Handles both first ask and subsequent callback queries"""
    if isinstance(update_or_query, Update):
        user_id = str(update_or_query.effective_user.id)
        send_func = update_or_query.message.reply_text
    else:  # CallbackQuery
        user_id = str(update_or_query.from_user.id)
        send_func = update_or_query.message.reply_text

    player = players[user_id]
    idx = player["q_index"]

    if idx < len(questions):
        q, options, _ = questions[idx]
        keyboard = [
            [InlineKeyboardButton(opt, callback_data=str(i))] for i, opt in enumerate(options)
        ]
        await send_func(q, reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_QUESTIONS
    else:
        return await finish_quiz(update_or_query, context)

async def handle_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    player = players[user_id]
    idx = player["q_index"]

    if idx < len(questions):
        _, _, correct_idx = questions[idx]
        chosen = int(query.data)

        if chosen == correct_idx:
            player["score"] += 1
            await query.edit_message_text("✅ Correct!")
        else:
            correct_text = questions[idx][1][correct_idx]
            await query.edit_message_text(f"❌ Wrong! Correct: {correct_text}")

        player["q_index"] += 1
        save_state()
        return await ask_question(query, context)

async def finish_quiz(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update_or_query.effective_user.id)
    player = players[user_id]

    leaderboard = sorted(players.values(), key=lambda x: x["score"], reverse=True)
    lines = ["🏆 Leaderboard:"]
    for i, p in enumerate(leaderboard[:10], start=1):
        lines.append(f"{i}. {p['name']} - {p['score']}")

    user_rank = leaderboard.index(player) + 1
    if user_rank > 10:
        lines.append("...")
        lines.append(f"{user_rank}. {player['name']} - {player['score']}")

    msg = f"Quiz finished! 🎉\nYour score: {player['score']}\n\n" + "\n".join(lines)

    if isinstance(update_or_query, Update):
        await update_or_query.message.reply_text(msg)
    else:
        await update_or_query.message.reply_text(msg)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Quiz cancelled. Use /start to try again.")
    return ConversationHandler.END

def main():
    load_state()
    TOKEN = os.getenv("BOT_TOKEN")
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_QUESTIONS: [CallbackQueryHandler(handle_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)

    port = int(os.environ.get("PORT", 8443))
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=TOKEN,
        webhook_url=f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME')}/{TOKEN}",
    )

if __name__ == "__main__":
    main()
