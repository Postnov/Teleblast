# Makefile для управления TeleBlast
# Используй: make <команда>

.PHONY: help start-bot stop-bot restart-bot start-webapp stop-webapp restart-webapp status logs clean install setup create-venv force-stop kill-safe kill-teleblast cleanup-pids

# Цвета для вывода
GREEN=\033[0;32m
YELLOW=\033[1;33m
RED=\033[0;31m
NC=\033[0m # No Color

# Рабочая директория проекта
PROJECT_DIR := /home/teleblast
BOT_PIDFILE := $(PROJECT_DIR)/bot.pid
WEBAPP_PIDFILE := $(PROJECT_DIR)/webapp.pid

# Помощь (команда по умолчанию)
help:
	@echo "$(GREEN)🤖 TeleBlast - Команды управления$(NC)"
	@echo ""
	@echo "$(YELLOW)📱 Управление ботом:$(NC)"
	@echo "  make start-bot     - Запустить бота в фоне"
	@echo "  make stop-bot      - Остановить бота"
	@echo "  make restart-bot   - Перезапустить бота"
	@echo ""
	@echo "$(YELLOW)🌐 Управление веб-панелью:$(NC)"
	@echo "  make start-webapp  - Запустить веб-интерфейс"
	@echo "  make stop-webapp   - Остановить веб-интерфейс"
	@echo "  make restart-webapp- Перезапустить веб-интерфейс"
	@echo ""
	@echo "$(YELLOW)📊 Мониторинг:$(NC)"
	@echo "  make status        - Показать статус всех процессов"
	@echo "  make logs          - Показать логи бота"
	@echo "  make logs-webapp   - Показать логи веб-приложения"
	@echo ""
	@echo "$(YELLOW)🔧 Утилиты:$(NC)"
	@echo "  make install       - Установить зависимости"
	@echo "  make setup         - Первичная настройка проекта"
	@echo "  make create-venv   - Создать виртуальное окружение"
	@echo "  make clean         - Очистить логи"
	@echo "  make stop-all      - Остановить все процессы"
	@echo "  make force-stop    - Принудительно остановить процессы проекта"
	@echo "  make kill-safe     - Безопасная остановка только bot.py и app.py"
	@echo "  make kill-teleblast   - Остановить все процессы TeleBlast в $(PROJECT_DIR)"
	@echo "  make cleanup-pids  - Очистить устаревшие pidfiles"
	@echo "  make start-all     - Запустить бота и веб-панель"

# === УПРАВЛЕНИЕ БОТОМ ===

start-bot:
	@echo "$(GREEN)🚀 Запускаем бота в фоновом режиме...$(NC)"
	@if [ -f "$(BOT_PIDFILE)" ]; then \
		pid=$$(cat $(BOT_PIDFILE) 2>/dev/null || echo ""); \
		if [ -n "$$pid" ] && kill -0 $$pid 2>/dev/null; then \
			echo "$(YELLOW)⚠️  Бот уже запущен! PID: $$pid$(NC)"; \
			exit 0; \
		else \
			echo "$(YELLOW)🧹 Удаляю устаревший pidfile$(NC)"; \
			rm -f $(BOT_PIDFILE); \
		fi; \
	fi; \
	nohup python3 bot.py > bot.log 2>&1 & \
	bot_pid=$$!; \
	echo $$bot_pid > $(BOT_PIDFILE); \
	echo "$(GREEN)✅ Бот запущен! PID: $$bot_pid$(NC)"; \
	echo "$(YELLOW)📄 Логи: tail -f bot.log$(NC)"

stop-bot:
	@echo "$(RED)⏹️  Останавливаем бота...$(NC)"
	@if [ -f "$(BOT_PIDFILE)" ]; then \
		pid=$$(cat $(BOT_PIDFILE) 2>/dev/null || echo ""); \
		if [ -n "$$pid" ] && kill -0 $$pid 2>/dev/null; then \
			echo "  🔍 Останавливаем бота PID: $$pid"; \
			kill -TERM $$pid 2>/dev/null || true; \
			sleep 2; \
			if kill -0 $$pid 2>/dev/null; then \
				echo "  🔍 Принудительно останавливаем PID: $$pid"; \
				kill -KILL $$pid 2>/dev/null || true; \
			fi; \
			rm -f $(BOT_PIDFILE); \
			echo "$(GREEN)✅ Бот остановлен$(NC)"; \
		else \
			echo "$(YELLOW)⚠️  Процесс бота не найден, удаляю pidfile$(NC)"; \
			rm -f $(BOT_PIDFILE); \
		fi; \
	else \
		echo "$(YELLOW)⚠️  Бот не запущен (pidfile не найден)$(NC)"; \
	fi

restart-bot: stop-bot
	@sleep 2
	@$(MAKE) start-bot

# === УПРАВЛЕНИЕ ВЕБ-ПРИЛОЖЕНИЕМ ===

start-webapp:
	@echo "$(GREEN)🌐 Запускаем веб-приложение...$(NC)"
	@python3 start_webapp.py

stop-webapp:
	@echo "$(RED)⏹️  Останавливаем веб-приложение...$(NC)"
	@webapp_stopped="no"; \
	for pid in $$(pgrep -f "python.*app.py" 2>/dev/null || true); do \
		if [ -n "$$pid" ] && [ "$$pid" -gt 0 ]; then \
			cwd=$$(readlink -f /proc/$$pid/cwd 2>/dev/null || echo ""); \
			if [ "$$cwd" = "$(PROJECT_DIR)" ] || [ "$$cwd" = "$(PROJECT_DIR)/webapp" ]; then \
				echo "  🔍 Останавливаем веб-приложение PID $$pid"; \
				kill -TERM $$pid 2>/dev/null || true; \
				sleep 1; \
				kill -KILL $$pid 2>/dev/null || true; \
				webapp_stopped="yes"; \
			fi; \
		fi; \
	done; \
	for pid in $$(pgrep -f "uvicorn.*app:app" 2>/dev/null || true); do \
		if [ -n "$$pid" ] && [ "$$pid" -gt 0 ]; then \
			cwd=$$(readlink -f /proc/$$pid/cwd 2>/dev/null || echo ""); \
			if [ "$$cwd" = "$(PROJECT_DIR)" ] || [ "$$cwd" = "$(PROJECT_DIR)/webapp" ]; then \
				echo "  🔍 Останавливаем uvicorn PID $$pid"; \
				kill -TERM $$pid 2>/dev/null || true; \
				sleep 1; \
				kill -KILL $$pid 2>/dev/null || true; \
				webapp_stopped="yes"; \
			fi; \
		fi; \
	done; \
	if [ "$$webapp_stopped" = "yes" ]; then \
		echo "$(GREEN)✅ Веб-приложение остановлено$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  Веб-приложение не запущено в $(PROJECT_DIR)$(NC)"; \
	fi

restart-webapp: stop-webapp
	@sleep 2
	@$(MAKE) start-webapp

# === МАССОВЫЕ ОПЕРАЦИИ ===

start-all:
	@echo "$(GREEN)🚀 Запускаем все сервисы...$(NC)"
	@$(MAKE) start-bot
	@sleep 1
	@echo "$(GREEN)🌐 Запуск веб-панели (нажмите Ctrl+C для остановки)...$(NC)"
	@$(MAKE) start-webapp

stop-all:
	@echo "$(RED)⏹️  Останавливаем все процессы...$(NC)"
	@$(MAKE) stop-bot
	@$(MAKE) stop-webapp

restart-all: stop-all
	@sleep 2
	@$(MAKE) start-all

# === МОНИТОРИНГ ===

status:
	@echo "$(GREEN)📊 Статус процессов в $(PROJECT_DIR):$(NC)"
	@echo ""
	@if [ -f "$(BOT_PIDFILE)" ]; then \
		pid=$$(cat $(BOT_PIDFILE) 2>/dev/null || echo ""); \
		if [ -n "$$pid" ] && kill -0 $$pid 2>/dev/null; then \
			echo "$(GREEN)🤖 Бот: ✅ Запущен (PID: $$pid)$(NC)"; \
		else \
			echo "$(RED)🤖 Бот: ❌ Остановлен (устаревший pidfile)$(NC)"; \
		fi; \
	else \
		echo "$(RED)🤖 Бот: ❌ Остановлен$(NC)"; \
	fi
	@webapp_pids=""; \
	webapp_count=0; \
	for pid in $$(pgrep -f "python.*app.py" 2>/dev/null || true); do \
		cwd=$$(readlink -f /proc/$$pid/cwd 2>/dev/null || echo ""); \
		if [ "$$cwd" = "$(PROJECT_DIR)" ] || [ "$$cwd" = "$(PROJECT_DIR)/webapp" ]; then \
			webapp_pids="$$webapp_pids $$pid"; \
			webapp_count=$$((webapp_count + 1)); \
		fi; \
	done; \
	uvicorn_pids=""; \
	uvicorn_count=0; \
	for pid in $$(pgrep -f "uvicorn.*app:app" 2>/dev/null || true); do \
		cwd=$$(readlink -f /proc/$$pid/cwd 2>/dev/null || echo ""); \
		if [ "$$cwd" = "$(PROJECT_DIR)" ] || [ "$$cwd" = "$(PROJECT_DIR)/webapp" ]; then \
			uvicorn_pids="$$uvicorn_pids $$pid"; \
			uvicorn_count=$$((uvicorn_count + 1)); \
		fi; \
	done; \
	if [ "$$webapp_count" -gt 0 ]; then \
		if [ "$$webapp_count" -eq 1 ]; then \
			echo "$(GREEN)🌐 Веб-панель: ✅ Запущена (PID:$$webapp_pids)$(NC)"; \
		else \
			echo "$(RED)🌐 Веб-панель: ⚠️  МНОЖЕСТВЕННЫЙ ЗАПУСК! ($$webapp_count экземпляров:$$webapp_pids)$(NC)"; \
		fi \
	elif [ "$$uvicorn_count" -gt 0 ]; then \
		if [ "$$uvicorn_count" -eq 1 ]; then \
			echo "$(GREEN)🌐 Веб-панель: ✅ Запущена uvicorn (PID:$$uvicorn_pids)$(NC)"; \
		else \
			echo "$(RED)🌐 Веб-панель: ⚠️  МНОЖЕСТВЕННЫЙ uvicorn! ($$uvicorn_count экземпляров:$$uvicorn_pids)$(NC)"; \
		fi \
	elif lsof -ti:8000 > /dev/null 2>&1; then \
		echo "$(YELLOW)🌐 Веб-панель: ⚠️  Порт 8000 занят (PID: $$(lsof -ti:8000))$(NC)"; \
	else \
		echo "$(RED)🌐 Веб-панель: ❌ Остановлена$(NC)"; \
	fi
	@echo ""
	@echo "$(YELLOW)📁 Файлы логов:$(NC)"
	@if [ -f bot.log ]; then echo "  📄 bot.log ($$(wc -l < bot.log) строк)"; fi
	@if [ -f webapp.log ]; then echo "  📄 webapp.log ($$(wc -l < webapp.log) строк)"; fi

cleanup-pids:
	@echo "$(YELLOW)🧹 Очистка устаревших pidfiles...$(NC)"
	@if [ -f "$(BOT_PIDFILE)" ]; then \
		pid=$$(cat $(BOT_PIDFILE) 2>/dev/null || echo ""); \
		if [ -n "$$pid" ] && kill -0 $$pid 2>/dev/null; then \
			echo "  ℹ️  bot.pid содержит активный процесс $$pid"; \
		else \
			rm -f $(BOT_PIDFILE); \
			echo "  🗑️  Удален устаревший bot.pid"; \
		fi; \
	else \
		echo "  ℹ️  bot.pid не найден"; \
	fi
	@if [ -f "$(WEBAPP_PIDFILE)" ]; then \
		pid=$$(cat $(WEBAPP_PIDFILE) 2>/dev/null || echo ""); \
		if [ -n "$$pid" ] && kill -0 $$pid 2>/dev/null; then \
			echo "  ℹ️  webapp.pid содержит активный процесс $$pid"; \
		else \
			rm -f $(WEBAPP_PIDFILE); \
			echo "  🗑️  Удален устаревший webapp.pid"; \
		fi; \
	else \
		echo "  ℹ️  webapp.pid не найден"; \
	fi
	@echo "$(GREEN)✅ Очистка завершена$(NC)"

logs:
	@echo "$(GREEN)📄 Последние 50 строк лога бота:$(NC)"
	@if [ -f bot.log ]; then \
		tail -50 bot.log; \
	else \
		echo "$(YELLOW)⚠️  Файл bot.log не найден$(NC)"; \
	fi

logs-webapp:
	@echo "$(GREEN)📄 Последние 50 строк лога веб-приложения:$(NC)"
	@if [ -f webapp.log ]; then \
		tail -50 webapp.log; \
	else \
		echo "$(YELLOW)⚠️  Файл webapp.log не найден$(NC)"; \
	fi

logs-live:
	@echo "$(GREEN)📄 Отслеживание логов бота в реальном времени (Ctrl+C для выхода):$(NC)"
	@tail -f bot.log

# === УТИЛИТЫ ===

install:
	@echo "$(GREEN)📦 Устанавливаем зависимости...$(NC)"
	@pip install -r requirements.txt
	@echo "$(GREEN)✅ Зависимости установлены$(NC)"

setup:
	@echo "$(GREEN)🔧 Первичная настройка проекта...$(NC)"
	@if [ ! -f .env ]; then \
		cp env.example .env && echo "$(GREEN)✅ Создан .env файл из примера$(NC)"; \
		echo "$(YELLOW)⚠️  Не забудьте настроить переменные в .env!$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  Файл .env уже существует$(NC)"; \
	fi
	@$(MAKE) create-venv
	@$(MAKE) install
	@echo "$(GREEN)✅ Настройка завершена!$(NC)"
	@echo "$(YELLOW)📝 Следующие шаги:$(NC)"
	@echo "  1. Настройте .env файл"
	@echo "  2. source venv/bin/activate"
	@echo "  3. make start-bot"
	@echo "  4. make start-webapp"

create-venv:
	@echo "$(GREEN)🐍 Создаём виртуальное окружение...$(NC)"
	@if [ ! -d venv ]; then \
		python3 -m venv venv && echo "$(GREEN)✅ Виртуальное окружение создано$(NC)"; \
		echo "$(YELLOW)💡 Активируйте его: source venv/bin/activate$(NC)"; \
	else \
		echo "$(YELLOW)⚠️  Виртуальное окружение уже существует$(NC)"; \
	fi

force-stop:
	@echo "$(RED)🚨 Принудительная остановка всех процессов...$(NC)"
	@echo "$(YELLOW)⚠️  Поиск и принудительная остановка процессов...$(NC)"
	@bot_pids=$$(pgrep -f "python.*bot.py" 2>/dev/null || true); \
	if [ -n "$$bot_pids" ]; then \
		echo "  🔍 Найдено ботов: $$bot_pids"; \
		for pid in $$bot_pids; do \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
		echo "  ✅ Процессы бота остановлены"; \
	else \
		echo "  ℹ️  Процессы бота не найдены"; \
	fi
	@webapp_pids=$$(pgrep -f "python.*app.py" 2>/dev/null || true); \
	if [ -n "$$webapp_pids" ]; then \
		echo "  🔍 Найдено веб-приложений: $$webapp_pids"; \
		for pid in $$webapp_pids; do \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
		echo "  ✅ Процессы веб-приложения остановлены"; \
	else \
		echo "  ℹ️  Процессы веб-приложения не найдены"; \
	fi
	@uvicorn_pids=$$(pgrep -f "uvicorn" 2>/dev/null || true); \
	if [ -n "$$uvicorn_pids" ]; then \
		echo "  🔍 Найдено uvicorn: $$uvicorn_pids"; \
		for pid in $$uvicorn_pids; do \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
		echo "  ✅ Процессы uvicorn остановлены"; \
	else \
		echo "  ℹ️  Процессы uvicorn не найдены"; \
	fi
	@echo "$(YELLOW)⚠️  Поиск связанных процессов...$(NC)"
	@other_pids=$$(pgrep -f "start_webapp.py\|remake-bot" 2>/dev/null || true); \
	if [ -n "$$other_pids" ]; then \
		echo "  🔍 Найдено связанных процессов: $$other_pids"; \
		for pid in $$other_pids; do \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
		echo "  ✅ Связанные процессы остановлены"; \
	else \
		echo "  ℹ️  Связанные процессы не найдены"; \
	fi
	@sleep 2
	@echo "$(GREEN)✅ Команда завершена$(NC)"
	@echo "$(YELLOW)🔍 Проверяем результат...$(NC)"
	@$(MAKE) status

kill-safe:
	@echo "$(YELLOW)🛡️  Безопасная остановка только bot.py и app.py...$(NC)"
	@echo "$(YELLOW)⚠️  Поиск процессов bot.py...$(NC)"
	@bot_pids=$$(ps aux | grep "[p]ython.*bot\.py" | awk '{print $$2}' || true); \
	if [ -n "$$bot_pids" ]; then \
		echo "  🔍 Найдено bot.py процессов: $$bot_pids"; \
		for pid in $$bot_pids; do \
			echo "    Останавливаем PID $$pid..."; \
			kill -15 $$pid 2>/dev/null || true; \
			sleep 1; \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
		echo "  ✅ Все bot.py процессы остановлены"; \
	else \
		echo "  ℹ️  Процессы bot.py не найдены"; \
	fi
	@echo "$(YELLOW)⚠️  Поиск процессов app.py...$(NC)"
	@app_pids=$$(ps aux | grep "[p]ython.*app\.py" | awk '{print $$2}' || true); \
	if [ -n "$$app_pids" ]; then \
		echo "  🔍 Найдено app.py процессов: $$app_pids"; \
		for pid in $$app_pids; do \
			echo "    Останавливаем PID $$pid..."; \
			kill -15 $$pid 2>/dev/null || true; \
			sleep 1; \
			kill -9 $$pid 2>/dev/null || true; \
		done; \
		echo "  ✅ Все app.py процессы остановлены"; \
	else \
		echo "  ℹ️  Процессы app.py не найдены"; \
	fi
	@sleep 1
	@echo "$(GREEN)✅ Безопасная остановка завершена$(NC)"
	@$(MAKE) status

kill-teleblast:
	@echo "$(RED)🚨 Останавливаем только TeleBlast (cwd=$(PROJECT_DIR))...$(NC)"
	@found="no"; \
	for pid in $$(pgrep -f "python.*bot.py" 2>/dev/null || true); do \
		if [ -n "$$pid" ] && [ "$$pid" -gt 0 ]; then \
			cwd=$$(readlink -f /proc/$$pid/cwd 2>/dev/null || echo ""); \
			if [ "$$cwd" = "$(PROJECT_DIR)" ]; then \
				found="yes"; \
				echo "  🔍 PID $$pid (бот, cwd=$$cwd) — останавливаю"; \
				kill -TERM $$pid 2>/dev/null || true; \
				sleep 1; \
				kill -KILL $$pid 2>/dev/null || true; \
			fi; \
		fi; \
	done; \
	for pid in $$(pgrep -f "python.*app.py" 2>/dev/null || true); do \
		if [ -n "$$pid" ] && [ "$$pid" -gt 0 ]; then \
			cwd=$$(readlink -f /proc/$$pid/cwd 2>/dev/null || echo ""); \
			if [ "$$cwd" = "$(PROJECT_DIR)" ] || [ "$$cwd" = "$(PROJECT_DIR)/webapp" ]; then \
				found="yes"; \
				echo "  🔍 PID $$pid (веб-панель, cwd=$$cwd) — останавливаю"; \
				kill -TERM $$pid 2>/dev/null || true; \
				sleep 1; \
				kill -KILL $$pid 2>/dev/null || true; \
			fi; \
		fi; \
	done; \
	if [ "$$found" = "no" ]; then \
		echo "  ℹ️  Процессов TeleBlast не найдено"; \
	else \
		echo "  ✅ Все процессы TeleBlast остановлены"; \
	fi
	@$(MAKE) status

clean:
	@echo "$(YELLOW)🧹 Очищаем логи и pidfiles...$(NC)"
	@if [ -f bot.log ]; then > bot.log && echo "$(GREEN)✅ bot.log очищен$(NC)"; fi
	@if [ -f webapp.log ]; then > webapp.log && echo "$(GREEN)✅ webapp.log очищен$(NC)"; fi
	@rm -f $(BOT_PIDFILE) $(WEBAPP_PIDFILE) && echo "$(GREEN)✅ pidfiles удалены$(NC)" || true
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "$(GREEN)✅ Очистка завершена$(NC)"

# === РАЗРАБОТКА ===

dev-bot:
	@echo "$(GREEN)🔧 Запускаем бота в режиме разработки...$(NC)"
	@python3 bot.py

dev-webapp:
	@echo "$(GREEN)🔧 Запускаем веб-приложение в режиме разработки...$(NC)"
	@cd webapp && python3 app.py 