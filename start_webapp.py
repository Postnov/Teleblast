#!/usr/bin/env python3
"""
Скрипт для запуска веб-приложения административной панели
"""
import os
import sys
import subprocess

def main():
    # Переходим в директорию webapp
    webapp_dir = os.path.join(os.path.dirname(__file__), 'webapp')
    
    if not os.path.exists(webapp_dir):
        print("❌ Директория webapp не найдена!")
        sys.exit(1)
    
    # Проверяем что виртуальное окружение активировано
    if not hasattr(sys, 'real_prefix') and not (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        print("⚠️  Рекомендуется активировать виртуальное окружение:")
        print("source venv/bin/activate")
        print()
    
    print("🚀 Запускаем веб-приложение...")
    print("📊 Административная панель будет доступна на http://localhost:8000")
    print("🔒 Не забудьте настроить .env файл с паролями!")
    print("⏹️  Для остановки нажмите Ctrl+C")
    print("-" * 50)
    
    try:
        # Запускаем приложение
        os.chdir(webapp_dir)
        subprocess.run([sys.executable, "app.py"], check=True)
    except KeyboardInterrupt:
        print("\n✅ Веб-приложение остановлено")
    except FileNotFoundError:
        print("❌ Файл webapp/app.py не найден!")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Ошибка при запуске: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 