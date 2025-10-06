import g4f
import logging
import json
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = "8378492013:AAEO9nI6gHqC3JPSiY9NrJbf8qti_zdTWHM"

ADMIN_IDS = [7963125435]
BANNED_USERS = set()

class DatabaseManager:
    def __init__(self, db_name="svai_bot.db"):
        self.db_name = db_name
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                last_activity TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tokens_used INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_by INTEGER,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

    def add_user(self, user_id, username, first_name, last_name):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_activity)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name, datetime.now()))
        conn.commit()
        conn.close()

    def increment_message_count(self, user_id):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE users SET message_count = message_count + 1, last_activity = ?
            WHERE user_id = ?
        ''', (datetime.now(), user_id))
        conn.commit()
        conn.close()

    def get_user_stats(self, user_id):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT message_count, created_at FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result

    def save_conversation(self, user_id, role, content, tokens=0):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (user_id, role, content, tokens_used)
            VALUES (?, ?, ?, ?)
        ''', (user_id, role, content, tokens))
        conn.commit()
        conn.close()

    def get_conversation_history(self, user_id, limit=10):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content FROM conversations 
            WHERE user_id = ? 
            ORDER BY timestamp DESC 
            LIMIT ?
        ''', (user_id, limit))
        results = cursor.fetchall()
        conn.close()
        return results[::-1]

    def clear_conversation_history(self, user_id):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM conversations WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()

    def ban_user(self, user_id, reason, banned_by, days=0):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        expires_at = None
        if days > 0:
            expires_at = datetime.now() + timedelta(days=days)
        
        cursor.execute('''
            INSERT OR REPLACE INTO banned_users (user_id, reason, banned_by, expires_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, reason, banned_by, expires_at))
        conn.commit()
        conn.close()
        BANNED_USERS.add(user_id)

    def unban_user(self, user_id):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM banned_users WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        BANNED_USERS.discard(user_id)

    def is_user_banned(self, user_id):
        if user_id in BANNED_USERS:
            return True
        
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT expires_at FROM banned_users 
            WHERE user_id = ? AND (expires_at IS NULL OR expires_at > ?)
        ''', (user_id, datetime.now()))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            BANNED_USERS.add(user_id)
            return True
        return False

    def get_all_users(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, username, first_name, message_count FROM users ORDER BY message_count DESC')
        results = cursor.fetchall()
        conn.close()
        return results

    def get_banned_users(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT bu.user_id, u.username, bu.reason, bu.banned_by, bu.banned_at, bu.expires_at
            FROM banned_users bu
            LEFT JOIN users u ON bu.user_id = u.user_id
        ''')
        results = cursor.fetchall()
        conn.close()
        return results

class SVAIProvider:
    def __init__(self):
        self.providers = self._get_available_providers()
        self.current_provider_index = 0

    def _get_available_providers(self):
        available = []
        for attr_name in dir(g4f.Provider):
            if not attr_name.startswith('_'):
                provider = getattr(g4f.Provider, attr_name)
                if isinstance(provider, type):
                    available.append(provider)
        return available

    async def get_response(self, messages):
        for attempt in range(len(self.providers)):
            provider = self.providers[self.current_provider_index]
            try:
                logger.info(f"Trying provider: {provider.__name__}")
                
                response = await g4f.ChatCompletion.create_async(
                    model=g4f.models.gpt_4,
                    messages=messages,
                    provider=provider,
                    timeout=60
                )
                
                if response and len(response.strip()) > 5:
                    logger.info(f"Success with provider: {provider.__name__}")
                    return response.strip()
                    
            except Exception as e:
                logger.warning(f"Provider {provider.__name__} failed: {str(e)[:100]}")
            
            self.current_provider_index = (self.current_provider_index + 1) % len(self.providers)
            await asyncio.sleep(1)
        
        return "❌ Все провайдеры временно недоступны. Пожалуйста, попробуйте позже."

class SVAIBot:
    def __init__(self):
        self.db = DatabaseManager()
        self.provider = SVAIProvider()
        self.system_prompt = """Ты svAI - продвинутый AI ассистент нового поколения. Твои характеристики:

🎯 ОСНОВНЫЕ ПРИНЦИПЫ:
- Отвечай точно, информативно и по существу
- Сохраняй профессиональный но дружелюбный тон
- Будь креативным в решениях проблем
- Адаптируйся под стиль общения пользователя

🚀 ВОЗМОЖНОСТИ:
- Глубокий анализ сложных вопросов
- Генерация качественного контента
- Технические консультации
- Творческие задачи
- Образовательная поддержка

📝 СТИЛЬ ОБЩЕНИЯ:
- Четкий и структурированный ответ
- Используй эмодзи для визуального оформления
- Разбивай сложные темы на понятные части
- Предлагай дополнительные идеи и варианты

Помни: твоя цель - быть максимально полезным инструментом для пользователя!"""

    async def process_message(self, user_id, message):
        if self.db.is_user_banned(user_id):
            return "🚫 Ваш доступ к боту ограничен. Для выяснения причин обратитесь к администратору."

        conversation_history = self.db.get_conversation_history(user_id, limit=6)
        
        messages = [{"role": "system", "content": self.system_prompt}]
        
        for role, content in conversation_history:
            messages.append({"role": role, "content": content})
        
        messages.append({"role": "user", "content": message})

        response = await self.provider.get_response(messages)
        
        if not response.startswith("❌"):
            self.db.save_conversation(user_id, "user", message)
            self.db.save_conversation(user_id, "assistant", response)
            self.db.increment_message_count(user_id)

        return response

bot = SVAIBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    bot.db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = """🤖 *Добро пожаловать в svAI!*

*ОСНОВНЫЕ КОМАНДЫ:*
/start - Начать работу
/clear - Очистить историю диалога  
/stats - Ваша статистика
/svai - Информация о боте

*ВОЗМОЖНОСТИ:*
✅ Глубокий анализ и решение задач
✅ Креативная генерация контента
✅ Технические консультации
✅ Образовательная поддержка

Просто напишите ваш вопрос или задачу! ⚡"""

    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.message.from_user
    user_message = update.message.text

    if user_message.startswith('/'):
        return

    await update.message.chat.send_action(action="typing")
    
    response = await bot.process_message(user.id, user_message)
    await update.message.reply_text(response, parse_mode='Markdown')

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    bot.db.clear_conversation_history(user.id)
    await update.message.reply_text("🗑️ *История диалога очищена!*", parse_mode='Markdown')

async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    stats = bot.db.get_user_stats(user.id)
    
    if stats:
        message_count, created_at = stats
        stats_text = f"""📊 *Ваша статистика:*

*Сообщений отправлено:* {message_count}
*Дата регистрации:* {created_at.split()[0]}
*Статус:* ✅ Активен

Продолжайте в том же духе! 🚀"""
    else:
        stats_text = "❌ Статистика не найдена."
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def svai_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_text = """⚡ *svAI - Продвинутый AI Ассистент*

*ВЕРСИЯ:* 2.0
*МОДЕЛЬ:* ????
*БАЗА:* ??????

*ОСОБЕННОСТИ:*
🔹 Мульти-провайдерная архитектура
🔹 Полная история диалогов
🔹 Система банов пользователей
🔹 Подробная статистика
🔹 Профессиональные ответы

*РАЗРАБОТЧИК:* в описании
*СТАТУС:* ✅ Активен"""

    await update.message.reply_text(info_text, parse_mode='Markdown')

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    users = bot.db.get_all_users()
    banned_users = bot.db.get_banned_users()
    
    stats_text = f"""📈 *АДМИН СТАТИСТИКА*

*Всего пользователей:* {len(users)}
*Заблокированных:* {len(banned_users)}

*ТОП-5 пользователей:*
"""
    
    for i, (user_id, username, first_name, count) in enumerate(users[:5], 1):
        name = first_name or username or f"ID{user_id}"
        stats_text += f"{i}. {name}: {count} сообщ.\n"

    stats_text += f"\n*Общее кол-во сообщений:* {sum(user[3] for user in users)}"
    
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ Использование: /ban <user_id> <причина> [дни]")
        return

    try:
        target_user_id = int(context.args[0])
        reason = ' '.join(context.args[1:-1]) if len(context.args) > 2 else context.args[1]
        days = int(context.args[-1]) if context.args[-1].isdigit() else 0
        
        bot.db.ban_user(target_user_id, reason, user.id, days)
        
        duration = "навсегда" if days == 0 else f"на {days} дней"
        await update.message.reply_text(f"✅ Пользователь {target_user_id} заблокирован {duration}. Причина: {reason}")
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат user_id или дней")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    if not context.args:
        await update.message.reply_text("❌ Использование: /unban <user_id>")
        return

    try:
        target_user_id = int(context.args[0])
        bot.db.unban_user(target_user_id)
        await update.message.reply_text(f"✅ Пользователь {target_user_id} разблокирован.")
        
    except ValueError:
        await update.message.reply_text("❌ Неверный формат user_id")

async def list_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Недостаточно прав.")
        return

    banned_users = bot.db.get_banned_users()
    
    if not banned_users:
        await update.message.reply_text("🚫 Нет заблокированных пользователей.")
        return

    banned_text = "🚫 *ЗАБЛОКИРОВАННЫЕ ПОЛЬЗОВАТЕЛИ:*\n\n"
    
    for i, (user_id, username, reason, banned_by, banned_at, expires_at) in enumerate(banned_users, 1):
        name = username or f"ID{user_id}"
        duration = "Навсегда" if not expires_at else f"До {expires_at.split()[0]}"
        banned_text += f"{i}. {name} (ID: {user_id})\nПричина: {reason}\n{duration}\n\n"

    await update.message.reply_text(banned_text, parse_mode='Markdown')

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)

def main():
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("stats", show_stats))
    application.add_handler(CommandHandler("svai", svai_info))
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("ban", ban_user))
    application.add_handler(CommandHandler("unban", unban_user))
    application.add_handler(CommandHandler("list_banned", list_banned))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.add_error_handler(error_handler)
    
    logger.info("🤖 svAI Bot Mega Edition started successfully!")
    logger.info("📊 Database: SQLite3")
    logger.info("⚡ Providers: Multiple GPT-4")
    logger.info("👑 Admin system: Active")
    
    application.run_polling()

if __name__ == "__main__":
    main()