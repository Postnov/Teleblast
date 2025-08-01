import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Безопасный парсинг ADMIN_IDS
def parse_admin_ids():
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    if not admin_ids_str:
        return []
    
    admin_ids = []
    for x in admin_ids_str.split(","):
        x = x.strip()
        if x.isdigit():
            admin_ids.append(int(x))
        elif x:  # Если не пустое и не число
            print(f"⚠️  Предупреждение: '{x}' не является корректным ID администратора (пропущено)")
    return admin_ids

ADMIN_IDS = parse_admin_ids()
DATABASE_PATH = os.getenv("DATABASE_PATH")

# Настройки веб-интерфейса
WEBAPP_USERNAME = os.getenv("WEBAPP_USERNAME")
WEBAPP_PASSWORD = os.getenv("WEBAPP_PASSWORD")
WEBAPP_SECRET_KEY = os.getenv("WEBAPP_SECRET_KEY")

required_vars = {
    'BOT_TOKEN': BOT_TOKEN,
    'DATABASE_PATH': DATABASE_PATH,
    'WEBAPP_USERNAME': WEBAPP_USERNAME,
    'WEBAPP_PASSWORD': WEBAPP_PASSWORD,
    'WEBAPP_SECRET_KEY': WEBAPP_SECRET_KEY,
}
missing = [k for k, v in required_vars.items() if not v]
if missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}") 