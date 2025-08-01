#!/usr/bin/env python3
"""
Скрипт для полной очистки базы данных TeleBlast
ВНИМАНИЕ: Это необратимая операция!
"""

import asyncio
import sys
from pathlib import Path

from database import Database
from config import DATABASE_PATH


async def clear_all_data(db: Database):
    """Полная очистка всех данных из базы. ВНИМАНИЕ: необратимая операция!"""
    print("🗑️  Очищаю базу данных...")
    
    # Очищаем таблицы в правильном порядке (с учётом FK)
    await db.conn.execute("DELETE FROM broadcast_messages")
    print("   ✅ Очищена таблица broadcast_messages")
    
    await db.conn.execute("DELETE FROM broadcasts")
    print("   ✅ Очищена таблица broadcasts")
    
    await db.conn.execute("DELETE FROM list_groups")
    print("   ✅ Очищена таблица list_groups")
    
    await db.conn.execute("DELETE FROM groups")
    print("   ✅ Очищена таблица groups")
    
    await db.conn.execute("DELETE FROM lists")
    print("   ✅ Очищена таблица lists")
    
    await db.conn.commit()
    print("🎉 База данных полностью очищена!")


async def get_database_stats(db: Database) -> dict:
    """Получить статистику базы данных"""
    stats = {}
    
    # Количество групп
    cursor = await db.conn.execute("SELECT COUNT(*) FROM groups")
    stats['groups'] = (await cursor.fetchone())[0]
    
    # Количество сегментов
    cursor = await db.conn.execute("SELECT COUNT(*) FROM lists")
    stats['segments'] = (await cursor.fetchone())[0]
    
    # Количество связей
    cursor = await db.conn.execute("SELECT COUNT(*) FROM list_groups")
    stats['connections'] = (await cursor.fetchone())[0]
    
    # Количество рассылок
    cursor = await db.conn.execute("SELECT COUNT(*) FROM broadcasts")
    stats['broadcasts'] = (await cursor.fetchone())[0]
    
    # Количество сообщений рассылок
    cursor = await db.conn.execute("SELECT COUNT(*) FROM broadcast_messages")
    stats['messages'] = (await cursor.fetchone())[0]
    
    return stats


def confirm_action() -> bool:
    """Запрашивает подтверждение пользователя"""
    print("\n" + "="*60)
    print("⚠️  ВНИМАНИЕ! ЭТО УДАЛИТ ВСЕ ДАННЫЕ ИЗ БАЗЫ!")
    print("="*60)
    print("Будут удалены:")
    print("• Все группы")
    print("• Все сегменты")
    print("• Все рассылки")
    print("• Вся история сообщений")
    print("• Все связи между группами и сегментами")
    print("\n🚨 ЭТА ОПЕРАЦИЯ НЕОБРАТИМА!")
    print("="*60)
    
    while True:
        answer = input("\nВы точно хотите очистить базу данных? (да/нет): ").strip().lower()
        if answer in ['да', 'yes', 'y']:
            # Дополнительное подтверждение
            confirm = input("Напишите 'УДАЛИТЬ' заглавными буквами для подтверждения: ").strip()
            if confirm == 'УДАЛИТЬ':
                return True
            else:
                print("❌ Неверное подтверждение. Операция отменена.")
                return False
        elif answer in ['нет', 'no', 'n']:
            return False
        else:
            print("Пожалуйста, ответьте 'да' или 'нет'")


async def main():
    """Основная функция"""
    print("🔧 TeleBlast - Очистка базы данных")
    print("=" * 40)
    
    # Проверяем существование базы данных
    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        print(f"❌ База данных не найдена: {DATABASE_PATH}")
        print("Возможно, бот ещё ни разу не запускался.")
        return
    
    # Инициализируем подключение к БД
    db = Database(DATABASE_PATH)
    await db.init()
    
    try:
        # Показываем текущую статистику
        stats = await get_database_stats(db)
        
        print(f"📊 Текущая статистика базы данных:")
        print(f"   📁 Групп: {stats['groups']}")
        print(f"   🏷️  Сегментов: {stats['segments']}")
        print(f"   🔗 Связей: {stats['connections']}")
        print(f"   📬 Рассылок: {stats['broadcasts']}")
        print(f"   💬 Сообщений: {stats['messages']}")
        
        # Если база уже пустая
        total_records = sum(stats.values())
        if total_records == 0:
            print("\n✅ База данных уже пустая!")
            return
        
        # Запрашиваем подтверждение
        if not confirm_action():
            print("✅ Операция отменена пользователем.")
            return
        
        # Выполняем очистку
        print("\n🚀 Начинаю очистку...")
        await clear_all_data(db)
        
        # Проверяем результат
        new_stats = await get_database_stats(db)
        total_after = sum(new_stats.values())
        
        if total_after == 0:
            print("\n🎉 База данных успешно очищена!")
            print("Теперь у вас чистый экземпляр TeleBlast.")
        else:
            print(f"\n⚠️  Предупреждение: в базе остались записи ({total_after})")
            
    except Exception as e:
        print(f"\n❌ Ошибка при работе с базой данных: {e}")
        sys.exit(1)
    
    finally:
        # Закрываем соединение
        if hasattr(db, 'conn') and db.conn:
            await db.conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⏹️  Операция прервана пользователем.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)