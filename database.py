import aiosqlite
from typing import Optional

class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    async def init(self):
        self.conn = await aiosqlite.connect(self.path)
        # Включаем каскадное удаление внешних ключей
        await self.conn.execute("PRAGMA foreign_keys = ON")
        
        # Проверяем и добавляем поле deleted если его нет
        await self._migrate_add_deleted_field()
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
        """)
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            chat_id INTEGER PRIMARY KEY,
            title TEXT
        )
        """)
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS list_groups (
            list_id INTEGER,
            group_id INTEGER,
            PRIMARY KEY (list_id, group_id),
            FOREIGN KEY (list_id) REFERENCES lists(id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups(chat_id) ON DELETE CASCADE
        )
        """)
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER,
            content_type TEXT,
            content TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deleted INTEGER DEFAULT 0
        )
        """)
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_messages (
            broadcast_id INTEGER,
            chat_id INTEGER,
            message_id INTEGER,
            PRIMARY KEY (broadcast_id, chat_id)
        )
        """)
        await self.conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            added_by INTEGER,
            super_admin INTEGER DEFAULT 0
        )
        """)
        await self.conn.commit()
        # Проверяем и добавляем поле super_admin если его нет
        await self._migrate_add_super_admin_field()

    async def _migrate_add_deleted_field(self):
        """Миграция для добавления поля deleted в таблицу broadcasts"""
        try:
            # Проверяем, есть ли уже поле deleted
            cursor = await self.conn.execute("PRAGMA table_info(broadcasts)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            if 'deleted' not in column_names:
                await self.conn.execute("ALTER TABLE broadcasts ADD COLUMN deleted INTEGER DEFAULT 0")
                await self.conn.commit()
                print("✅ Поле 'deleted' добавлено в таблицу broadcasts")
        except Exception as e:
            print(f"❌ Ошибка миграции: {e}")

    async def _migrate_add_super_admin_field(self):
        """Миграция для добавления поля super_admin в таблицу admins"""
        try:
            cursor = await self.conn.execute("PRAGMA table_info(admins)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            if 'super_admin' not in column_names:
                await self.conn.execute("ALTER TABLE admins ADD COLUMN super_admin INTEGER DEFAULT 0")
                await self.conn.commit()
                print("✅ Поле 'super_admin' добавлено в таблицу admins")
        except Exception as e:
            print(f"❌ Ошибка миграции super_admin: {e}")

    async def create_list(self, name: str):
        await self.conn.execute("INSERT OR IGNORE INTO lists(name) VALUES (?)", (name,))
        await self.conn.commit()

    async def get_lists(self):
        cursor = await self.conn.execute("SELECT id, name FROM lists")
        return await cursor.fetchall()

    async def get_list_by_name(self, name: str):
        cursor = await self.conn.execute("SELECT id FROM lists WHERE name = ?", (name,))
        return await cursor.fetchone()

    async def add_group_to_list(self, list_name: str, chat_id: int, title: str):
        lst = await self.get_list_by_name(list_name)
        if not lst:
            await self.create_list(list_name)
            lst = await self.get_list_by_name(list_name)
        list_id = lst[0]
        await self.conn.execute("INSERT OR IGNORE INTO groups(chat_id, title) VALUES (?, ?)", (chat_id, title))
        await self.conn.execute("INSERT OR IGNORE INTO list_groups(list_id, group_id) VALUES (?, ?)", (list_id, chat_id))
        await self.conn.commit()

    async def get_groups_in_list(self, list_id: int):
        cursor = await self.conn.execute("SELECT group_id FROM list_groups WHERE list_id = ?", (list_id,))
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def record_broadcast(self, list_id: int, content_type: str, content: Optional[str]):
        cursor = await self.conn.execute(
            "INSERT INTO broadcasts(list_id, content_type, content) VALUES (?, ?, ?)",
            (list_id, content_type, content),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def record_broadcast_message(self, broadcast_id: int, chat_id: int, message_id: int):
        await self.conn.execute(
            "INSERT OR REPLACE INTO broadcast_messages(broadcast_id, chat_id, message_id) VALUES (?, ?, ?)",
            (broadcast_id, chat_id, message_id),
        )
        await self.conn.commit()

    async def get_last_broadcast_id(self):
        cursor = await self.conn.execute("SELECT id FROM broadcasts ORDER BY id DESC LIMIT 1")
        row = await cursor.fetchone()
        return row[0] if row else None

    async def get_recent_broadcasts(self, limit: int = 3):
        """Получить последние N рассылок вместе с названием сегмента"""
        cursor = await self.conn.execute(
            """
            SELECT b.id, b.date, l.name, b.content_type, b.content
            FROM broadcasts b
            LEFT JOIN lists l ON b.list_id = l.id
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()

    async def get_broadcast_messages(self, broadcast_id: int):
        cursor = await self.conn.execute(
            "SELECT chat_id, message_id FROM broadcast_messages WHERE broadcast_id = ?",
            (broadcast_id,),
        )
        return await cursor.fetchall()

    async def get_all_groups(self):
        cursor = await self.conn.execute("SELECT chat_id, title FROM groups")
        return await cursor.fetchall()

    async def get_unassigned_groups(self):
        cursor = await self.conn.execute("""
            SELECT g.chat_id, g.title 
            FROM groups g 
            LEFT JOIN list_groups lg ON g.chat_id = lg.group_id 
            WHERE lg.group_id IS NULL
        """)
        return await cursor.fetchall()

    async def assign_group_to_list(self, chat_id: int, list_id: int):
        await self.conn.execute("INSERT OR IGNORE INTO list_groups(list_id, group_id) VALUES (?, ?)", (list_id, chat_id))
        await self.conn.commit()

    async def delete_list(self, list_id: int):
        await self.conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
        await self.conn.commit()

    async def get_groups_with_lists(self):
        """Получить все группы с информацией о привязанных списках"""
        cursor = await self.conn.execute("""
            SELECT g.chat_id, g.title, GROUP_CONCAT(l.name, ', ') as list_names
            FROM groups g
            LEFT JOIN list_groups lg ON g.chat_id = lg.group_id
            LEFT JOIN lists l ON lg.list_id = l.id
            GROUP BY g.chat_id, g.title
            ORDER BY g.title
        """)
        return await cursor.fetchall()

    async def remove_group_from_list(self, chat_id: int, list_id: int):
        """Удалить группу из списка"""
        await self.conn.execute("DELETE FROM list_groups WHERE group_id = ? AND list_id = ?", (chat_id, list_id))
        await self.conn.commit()

    async def delete_group(self, chat_id: int):
        """Полностью удалить группу из базы данных вместе с привязками"""
        # Удаляем привязки вручную (на случай если foreign_keys=OFF)
        await self.conn.execute("DELETE FROM list_groups WHERE group_id = ?", (chat_id,))
        await self.conn.execute("DELETE FROM groups WHERE chat_id = ?", (chat_id,))
        await self.conn.commit()

    async def add_group(self, chat_id: int, title: str):
        """Добавить группу в базу данных без привязки к списку"""
        await self.conn.execute("INSERT OR IGNORE INTO groups(chat_id, title) VALUES (?, ?)", (chat_id, title))
        await self.conn.commit()

    async def get_group_current_list(self, chat_id: int):
        """Получить текущий список группы"""
        cursor = await self.conn.execute("""
            SELECT l.id, l.name 
            FROM lists l
            JOIN list_groups lg ON l.id = lg.list_id
            WHERE lg.group_id = ?
        """, (chat_id,))
        return await cursor.fetchone()

    async def get_group_segments(self, chat_id: int):
        """Вернуть все сегменты для указанной группы"""
        cursor = await self.conn.execute(
            """
            SELECT l.name FROM lists l
            JOIN list_groups lg ON l.id = lg.list_id
            WHERE lg.group_id = ?
            ORDER BY l.name
            """,
            (chat_id,),
        )
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

    async def get_groups_in_list_detailed(self, list_id: int):
        """Получить подробную информацию о группах в списке"""
        cursor = await self.conn.execute("""
            SELECT g.chat_id, g.title
            FROM groups g
            JOIN list_groups lg ON g.chat_id = lg.group_id
            WHERE lg.list_id = ?
            ORDER BY g.title
        """, (list_id,))
        return await cursor.fetchall()

    async def get_broadcast_message_count(self, broadcast_id: int):
        """Получить количество сообщений в рассылке"""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM broadcast_messages WHERE broadcast_id = ?",
            (broadcast_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_recent_broadcasts_with_message_count(self, limit: int = 10):
        """Получить последние N рассылок вместе с количеством сообщений и статусом"""
        cursor = await self.conn.execute(
            """
            SELECT 
                b.id, 
                b.date, 
                l.name, 
                b.content_type, 
                b.content,
                COALESCE(msg_count.count, 0) as message_count,
                b.deleted
            FROM broadcasts b
            LEFT JOIN lists l ON b.list_id = l.id
            LEFT JOIN (
                SELECT broadcast_id, COUNT(*) as count 
                FROM broadcast_messages 
                GROUP BY broadcast_id
            ) msg_count ON b.id = msg_count.broadcast_id
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cursor.fetchall()

    async def mark_broadcast_as_deleted(self, broadcast_id: int):
        """Пометить рассылку как удаленную"""
        await self.conn.execute(
            "UPDATE broadcasts SET deleted = 1 WHERE id = ?",
            (broadcast_id,)
        )
        await self.conn.commit()

    # ---- Методы для работы с админами ---- #

    async def add_admin(self, user_id: int, username: str = None, first_name: str = None, added_by: int = None, super_admin: int = 0):
        """Добавить администратора"""
        await self.conn.execute(
            "INSERT OR REPLACE INTO admins (user_id, username, first_name, added_by, super_admin) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, first_name, added_by, super_admin)
        )
        await self.conn.commit()

    async def remove_admin(self, user_id: int):
        """Удалить администратора"""
        await self.conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await self.conn.commit()

    async def is_admin(self, user_id: int) -> bool:
        """Проверить, является ли пользователь администратором"""
        cursor = await self.conn.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row is not None

    async def is_super_admin(self, user_id: int) -> bool:
        cursor = await self.conn.execute("SELECT super_admin FROM admins WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row is not None and row[0] == 1

    async def get_all_admins(self):
        """Получить всех администраторов"""
        cursor = await self.conn.execute(
            "SELECT user_id, username, first_name, added_at FROM admins ORDER BY added_at"
        )
        return await cursor.fetchall()

    async def set_super_admin(self, new_super_id: int):
        """Передает статус супер админа другому пользователю"""
        # Снимаем текущий супер флаг
        await self.conn.execute("UPDATE admins SET super_admin = 0 WHERE super_admin = 1")
        # Назначаем нового
        await self.conn.execute("UPDATE admins SET super_admin = 1 WHERE user_id = ?", (new_super_id,))
        await self.conn.commit()

    async def migrate_admins_from_config(self, admin_ids: list):
        """Миграция админов из конфига в базу данных"""
        # Проверяем, есть ли уже супер-администратор в базе
        cursor = await self.conn.execute("SELECT user_id FROM admins WHERE super_admin = 1 LIMIT 1")
        row = await cursor.fetchone()
        has_super = row is not None

        for idx, admin_id in enumerate(admin_ids):
            # Если супер-админ уже есть – не назначаем нового при миграции
            super_flag = 1 if (idx == 0 and not has_super) else 0
            if not await self.is_admin(admin_id):
                await self.add_admin(admin_id, username="from_config", first_name="Legacy Admin", super_admin=super_flag)
            else:
                if super_flag == 1:
                    await self.set_super_admin(admin_id)
        await self.conn.commit() 