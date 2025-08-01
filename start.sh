#!/bin/bash

# Проверяем наличие обязательных переменных окружения
if [ -z "$BOT_TOKEN" ]; then
    echo "❌ Ошибка: BOT_TOKEN не установлен"
    exit 1
fi

if [ -z "$ADMIN_IDS" ]; then
    echo "❌ Ошибка: ADMIN_IDS не установлен"
    exit 1
fi

echo "🚀 Запуск Telegram бота..."
echo "📁 Рабочая директория: $(pwd)"
echo "🔑 BOT_TOKEN: ${BOT_TOKEN:0:10}..."
echo "👤 ADMIN_IDS: $ADMIN_IDS"

# Создаем базу данных если её нет
python -c "
import asyncio
from database import Database
async def init_db():
    db = Database('bot.db')
    await db.init()
    print('📊 База данных инициализирована')
asyncio.run(init_db())
"

# Запускаем основного бота
echo "🤖 Запуск основного бота..."
exec python bot.py 