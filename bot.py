import logging
import os
import json
import random
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

# States
ASK_NAME, ASK_QUESTIONS = range(2)

# Ephemeral state file (scores, progress)
STATE_FILE = "players.json"
players = {}  # user_id -> {name, score, q_index, quiz}

# Load questions from external JSON
with open("questions.json", "r", encoding="utf-8") as qfile:
    questions_pool = json.load(qfile)


# ---------- Persistence helpers ----------
def load_state():
    global players
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                players = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            players = {}
    else:
        players = {}


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(players, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


# ---------- Utility ----------
def get_user_id(obj: Union[Update, "CallbackQuery"]) -> str:
    if hasattr(obj, "from_user"):
        return str(obj.from_user.id)
    return str(obj.effective_user.id)


def reply_text_for(update_or_query):
    if hasattr(update_or_query, "message") and update_or_query.message:
        return update_or_query.message.reply_text
    return update_or_query.message.reply_text


# ---------- Handlers ----------
async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    # Reset session
    players[user_id] = {"name": "", "score": 0, "q_index": 0, "quiz": []}
    save_state()

    await update.message.reply_text("👋 خوش آمدید! لطفاً نام خود را وارد کنید:")
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    name = update.message.text.strip()

    # Pick 5 random questions for this user
    selected = random.sample(questions_pool, 5)

    players[user_id] = {
        "name": name,
        "score": 0,
        "q_index": 0,
        "quiz": selected,
    }
    save_state()

    await update.message.reply_text(f"سلام {name}! کوئیز شروع شد 🎉")
    return await ask_question(update, context)


async def ask_question(update_or_query: Union[Update, "CallbackQuery"], context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update_or_query)
    player = players[user_id]
    idx = player["q_index"]
    quiz = player["quiz"]

    if idx < len(quiz):
        q = quiz[idx]["question"]
        options = quiz[idx]["options"]
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
    await query.answer()
    user_id = str(query.from_user.id)
    player = players[user_id]
    idx = player["q_index"]
    quiz = player["quiz"]

    if idx >= len(quiz):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="کوئیز شما تمام شده است. برای شروع دوباره /start را بزنید."
        )
        return ConversationHandler.END

    correct_idx = quiz[idx]["answer"]
    chosen = int(query.data)
    correct_text = quiz[idx]["options"][correct_idx]

    if chosen == correct_idx:
        player["score"] += 1
        await query.edit_message_text("✅ درسته!")
    else:
        await query.edit_message_text(f"❌ نادرست! پاسخ صحیح: {correct_text}")

    player["q_index"] += 1
    save_state()

    return await ask_question(query, context)


async def finish_quiz(update_or_query: Union[Update, "CallbackQuery"], _context: ContextTypes.DEFAULT_TYPE):
    user_id = get_user_id(update_or_query)
    player = players[user_id]

    leaderboard = sorted(players.values(), key=lambda x: x["score"], reverse=True)
    lines = ["🏆 جدول امتیازات:"]
    for i, p in enumerate(leaderboard[:10], start=1):
        lines.append(f"{i}. {p['name']} - {p['score']}")

    user_rank = leaderboard.index(player) + 1
    if user_rank > 10:
        lines.append("...")
        lines.append(f"{user_rank}. {player['name']} - {player['score']}")

    msg = f"🎉 کوئیز با موفقیت به اتمام رسید!\nامتیاز شما: {player['score']}\n\n" + "\n".join(lines)
    send = reply_text_for(update_or_query)
    await send(msg)

    return ConversationHandler.END


async def cancel(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("کوئیز لغو شد. برای شروع دوباره /start را بزنید.")
    return ConversationHandler.END


async def reset(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    players.pop(user_id, None)
    save_state()
    await update.message.reply_text("✅ وضعیت کوئیز شما ریست شد. برای شروع دوباره /start را بزنید.")
    return ConversationHandler.END


async def leaderboard_cmd(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if not players:
        await update.message.reply_text("هنوز کسی در کوئیز شرکت نکرده است.")
        return
    leaderboard = sorted(players.values(), key=lambda x: x["score"], reverse=True)
    lines = ["🏆 جدول برترین‌ها:"]
    for i, p in enumerate(leaderboard[:10], start=1):
        lines.append(f"{i}. {p['name']} - {p['score']}")
    await update.message.reply_text("\n".join(lines))


# ---------- Error handler ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if isinstance(update, Update):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ خطایی رخ داد. لطفاً /start را بزنید تا دوباره شروع کنید."
            )
    except Exception as e:
        logger.error(f"Failed to notify user: {e}")


# ---------- Main ----------
def main():
    load_state()
    bot_token = os.getenv("bot_token")
    if not bot_token:
        raise RuntimeError("bot_token env variable is required.")

    app = Application.builder().token(bot_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_QUESTIONS: [CallbackQueryHandler(handle_answer)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_error_handler(error_handler)

    port = int(os.environ.get("PORT", 8443))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=bot_token,
        webhook_url=f"https://telequizbot.onrender.com/{bot_token}",
    )


if __name__ == "__main__":
    main()
