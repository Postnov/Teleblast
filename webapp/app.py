from fastapi import FastAPI, Request, Form, status, Depends, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import uvicorn
import os
import sys
import asyncio
from contextlib import asynccontextmanager
import secrets

# Добавляем родительскую директорию в sys.path для импорта config и database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DATABASE_PATH as DB_PATH_RELATIVE, WEBAPP_USERNAME, WEBAPP_PASSWORD  # Используем тот же путь БД, что и бот
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), DB_PATH_RELATIVE)
from database import Database
from typing import List, Optional
# Pydantic v2 supports Union directly
from typing import Union

db = Database(DB_PATH)
security = HTTPBasic()

def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
    """Проверка аутентификации для доступа к админ-панели"""
    is_correct_username = secrets.compare_digest(credentials.username, WEBAPP_USERNAME)
    is_correct_password = secrets.compare_digest(credentials.password, WEBAPP_PASSWORD)
    
    if not (is_correct_username and is_correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await db.init()
    yield
    # Shutdown (если нужно)

app = FastAPI(title="TeleBlast Admin", lifespan=lifespan)

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(WEBAPP_DIR, "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, credentials: HTTPBasicCredentials = Depends(authenticate)):
    """Главная страница с гибким фильтром.

    Query-параметры:
        include:  id списка (может повторяться) – группа ДОЛЖНА входить хотя бы в один
        exclude:  id списка (может повторяться) – группа НЕ ДОЛЖНА входить ни в один
        unassigned=1 – показывать только группы без списков
    """

    lists = await db.get_lists()
    list_dict = {str(lid): name for lid, name in lists}

    # Все группы – база
    all_groups = await db.get_groups_with_lists()
    total_groups = len(all_groups)

    # --- Извлекаем параметры фильтра --- #
    params = request.query_params
    include_ids = params.getlist("include")  # список строковых id
    exclude_ids = params.getlist("exclude")
    unassigned_only = params.get("unassigned") == "1"

    # Преобразуем в названия списков для удобства
    include_names = [list_dict.get(i) for i in include_ids if list_dict.get(i)]
    exclude_names = [list_dict.get(i) for i in exclude_ids if list_dict.get(i)]

    def group_list_names(row):
        return [n.strip() for n in row[2].split(",") if n.strip()] if row[2] else []

    groups = all_groups

    # Фильтр «только без списка»
    if unassigned_only:
        groups = [g for g in groups if not group_list_names(g)]
    else:
        # Применяем include / exclude
        if include_names:
            groups = [g for g in groups if any(name in group_list_names(g) for name in include_names)]
        if exclude_names:
            groups = [g for g in groups if all(name not in group_list_names(g) for name in exclude_names)]

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "lists": lists,
            "groups": groups,
            "total_groups": total_groups,
            "include_ids": include_ids,
            "exclude_ids": exclude_ids,
            "unassigned_only": unassigned_only,
        },
    )


# --- Lists --- #

@app.post("/lists/create")
async def create_list(name: str = Form(...), credentials: HTTPBasicCredentials = Depends(authenticate)):
    await db.create_list(name.strip())
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@app.post("/lists/{list_id}/delete")
async def delete_list(list_id: int, credentials: HTTPBasicCredentials = Depends(authenticate)):
    await db.delete_list(list_id)
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


# --- Groups --- #

@app.post("/groups/{chat_id}/delete")
async def delete_group(chat_id: int, credentials: HTTPBasicCredentials = Depends(authenticate)):
    await db.delete_group(chat_id)
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@app.post("/groups/{chat_id}/assign")
async def assign_group(chat_id: int, list_id: int = Form(...), credentials: HTTPBasicCredentials = Depends(authenticate)):
    await db.assign_group_to_list(chat_id, list_id)
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


@app.post("/groups/{chat_id}/unassign")
async def unassign_group(chat_id: int, list_id: int = Form(...), credentials: HTTPBasicCredentials = Depends(authenticate)):
    await db.remove_group_from_list(chat_id, list_id)
    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

# --- Bulk operations --- #

@app.post("/groups/bulk")
async def bulk_groups(request: Request, credentials: HTTPBasicCredentials = Depends(authenticate)):
    """Массовое добавление или удаление групп из списка, а также полное удаление групп.

    Обрабатываем чекбоксы корректно через request.form().getlist().
    """
    form = await request.form()
    action = form.get("action")
    chat_ids_list = form.getlist("chat_ids")

    if not chat_ids_list:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    if action == "assign":
        list_id = int(form.get("list_id"))
        for chat_id in chat_ids_list:
            await db.assign_group_to_list(int(chat_id), list_id)
    elif action == "unassign":
        list_id = int(form.get("list_id"))
        for chat_id in chat_ids_list:
            await db.remove_group_from_list(int(chat_id), list_id)
    elif action == "delete":
        # Полное удаление групп из базы данных
        for chat_id in chat_ids_list:
            await db.delete_group(int(chat_id))

    return RedirectResponse("/", status_code=status.HTTP_302_FOUND)


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
