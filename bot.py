import os
import logging
import json
import re
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Функция для извлечения ID таблицы из URL
def extract_sheet_id(url):
    pattern = r'/spreadsheets/d/([a-zA-Z0-9-_]+)'
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    return None

# Конфигурация
GOOGLE_SHEETS_URL = "https://docs.google.com/spreadsheets/d/1NyzGqlL4X3GMKUdElMpXOS00DN6230zGq_zpxfFmEjU/edit?gid=0"
SPREADSHEET_ID = extract_sheet_id(GOOGLE_SHEETS_URL)

if not SPREADSHEET_ID:
    logger.error("Не удалось извлечь ID таблицы из URL")
    raise ValueError("Неверный URL Google Sheets")

logger.info(f"Используется Google Sheets с ID: {SPREADSHEET_ID}")

# Проверка переменных окружения
try:
    TOKEN = os.environ['TELEGRAM_TOKEN']
    GOOGLE_CREDS_JSON = os.environ['GOOGLE_CREDS_JSON']
    logger.info("Проверка переменных окружения: OK")
except KeyError as e:
    logger.error(f"Отсутствует обязательная переменная окружения: {e}")
    raise

# Инициализация Google API
try:
    creds_info = json.loads(GOOGLE_CREDS_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=['https://www.googleapis.com/auth/spreadsheets', 
               'https://www.googleapis.com/auth/drive']
    )
    sheets_service = build('sheets', 'v4', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    logger.info("Инициализация Google API: OK")
except Exception as e:
    logger.error(f"Ошибка инициализации Google API: {e}")
    raise

photo_counter = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global photo_counter
    photo_counter = 1
    logger.info(f"Получена команда /start от {update.effective_user.id}")
    await update.message.reply_text(
        "Привет! Отправь мне до 21 фото, и я распределю их по таблице: "
        "нечетные — в столбец C, четные — в столбец D.\n\n"
        f"Ссылка на таблицу: {GOOGLE_SHEETS_URL}"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global photo_counter
    try:
        user = update.effective_user
        logger.info(f"Получено фото от {user.id} ({user.username})")
        
        photo = update.message.photo[-1]
        file = await photo.get_file()
        file_path = f"photos/{file.file_id}.jpg"
        
        os.makedirs("photos", exist_ok=True)
        await file.download_to_drive(file_path)
        logger.info(f"Фото сохранено: {file_path}")

        # Загрузка на Google Drive
        file_metadata = {'name': file.file_id}
        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        uploaded_file = drive_service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id'
        ).execute()
        file_id = uploaded_file.get('id')
        logger.info(f"Фото загружено на Drive, ID: {file_id}")

        # Создание публичной ссылки
        drive_service.permissions().create(
            fileId=file_id,
            body={'role': 'reader', 'type': 'anyone'}
        ).execute()
        photo_url = f"https://drive.google.com/uc?export=view&id={file_id}"

        # Определение столбца
        column = "C" if photo_counter % 2 == 1 else "D"
        logger.info(f"Фото №{photo_counter} -> столбец {column}")

        # Поиск первой пустой строки
        range_name = f"{column}2:{column}"
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
        ).execute()
        values = result.get('values', [])
        next_row = len(values) + 2 if values else 2

        # Сохранение в таблицу
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{column}{next_row}",
            valueInputOption="RAW",
            body={"values": [[photo_url]]},
        ).execute()
        logger.info(f"URL фото добавлен в таблицу: {column}{next_row}")

        await update.message.reply_text(
            f"Фото №{photo_counter} добавлено в таблицу!\n"
            f"Столбец: {column}\n"
            f"Ссылка: {GOOGLE_SHEETS_URL}"
        )

        photo_counter += 1
        if photo_counter > 21:
            await update.message.reply_text("Все 21 фото загружены. Счетчик сброшен.")
            photo_counter = 1

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}", exc_info=True)
        await update.message.reply_text(
            "Произошла ошибка при обработке фото. Пожалуйста, попробуйте еще раз.\n"
            f"Если проблема сохраняется, сообщите администратору.\n\n"
            f"Ошибка: {str(e)}"
        )

# Инициализация бота
try:
    logger.info("=== ИНИЦИАЛИЗАЦИЯ БОТА ===")
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    logger.info("Обработчики команд добавлены")
except Exception as e:
    logger.error(f"Ошибка инициализации бота: {e}")
    raise

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        application.process_update(update)
        return 'ok'
    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}")
        return 'error', 500

if __name__ == '__main__':
    try:
        logger.info("=== STARTING BOT ===")
        app = ApplicationBuilder().token(TOKEN).build()
        
        # Добавляем обработчики
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        
        # Режим для Render
        if os.getenv('RENDER'):
            logger.info("WEBHOOK MODE")
            url = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/webhook"
            app.run_webhook(
                listen="0.0.0.0",
                port=5000,
                url_path="TOKEN",
                webhook_url=url,
                secret_token='YOUR_SECRET_TOKEN'
            )
        else:
            logger.info("POLLING MODE")
            app.run_polling()
            
    except Exception as e:
        logger.error(f"FATAL ERROR: {str(e)}")
        raise