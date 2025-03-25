import os
import logging
import json
from flask import Flask, request
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Настройка Flask
app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка учетных данных Google из переменной окружения
GOOGLE_CREDS_JSON = os.getenv('GOOGLE_CREDS_JSON')
if not GOOGLE_CREDS_JSON:
    raise ValueError("Не найдена переменная окружения GOOGLE_CREDS_JSON")

# ID вашей Google Sheets таблицы
SPREADSHEET_ID = '1NyzGqlL4X3GMKUdElMpXOS00DN6230zGq_zpxfFmEjU'

# Авторизация в Google API
try:
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDS_JSON),
        scopes=['https://www.googleapis.com/auth/spreadsheets', 
               'https://www.googleapis.com/auth/drive']
    )
    sheets_service = build('sheets', 'v4', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
except Exception as e:
    logger.error(f"Ошибка авторизации Google: {e}")
    raise

# Счетчик для фотографий
photo_counter = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global photo_counter
    photo_counter = 1
    await update.message.reply_text(
        "Привет! Отправь мне до 21 фото, и я распределю их по таблице: "
        "нечетные — в столбец C, четные — в столбец D."
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global photo_counter
    try:
        # Скачивание фото
        photo = update.message.photo[-1]
        file = await photo.get_file()
        file_path = f"photos/{file.file_id}.jpg"
        await file.download_to_drive(file_path)

        # Загрузка на Google Drive
        file_metadata = {'name': file.file_id}
        media = MediaFileUpload(file_path, mimetype='image/jpeg')
        uploaded_file = drive_service.files().create(
            body=file_metadata, 
            media_body=media, 
            fields='id'
        ).execute()
        file_id = uploaded_file.get('id')

        # Создание публичной ссылки
        drive_service.permissions().create(
            fileId=file_id,
            body={'role': 'reader', 'type': 'anyone'}
        ).execute()
        photo_url = f"https://drive.google.com/uc?export=view&id={file_id}"

        # Определение столбца
        column = "C" if photo_counter % 2 == 1 else "D"

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

        photo_counter += 1
        if photo_counter > 21:
            await update.message.reply_text("Все фото загружены.")
            photo_counter = 1

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуй еще раз.")

# Инициализация бота
TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("Не найдена переменная окружения TELEGRAM_TOKEN")

application = ApplicationBuilder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.process_update(update)
    return 'ok'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)