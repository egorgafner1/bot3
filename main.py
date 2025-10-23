import logging
import time
import threading
from io import BytesIO
from PIL import Image
from threading import Lock
from collections import defaultdict
import telebot
import os
from dotenv import load_dotenv

load_dotenv()  # загружает переменные из .env

TOKEN = os.getenv('TELEGRAM_TOKEN')
if not TOKEN:
    raise ValueError("Токен не найден! Укажите TELEGRAM_TOKEN в .env")

# Инициализация бота
bot = telebot.TeleBot(TOKEN)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Глобальные структуры для хранения медиа-групп
media_groups = defaultdict(list)
media_group_lock = Lock()
media_group_timers = {}

# Таймаут ожидания всех частей альбома (в секундах)
GROUP_TIMEOUT = 2


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, """
Привет! Я бот, который может конвертировать изображения в PDF файлы.

Просто отправьте мне изображения, и я верну вам PDF файл.
""")

def create_pdf_from_images(images, chat_id, user_id):
    """Создаёт PDF из списка изображений и отправляет пользователю"""
    try:
        if not images:
            bot.send_message(chat_id, "Не удалось получить изображения.")
            return

        output_pdf = BytesIO()
        images[0].save(
            output_pdf,
            format='PDF',
            save_all=True,
            append_images=images[1:],
            resolution=100.0
        )
        output_pdf.seek(0)

        bot.send_document(chat_id, ('file.pdf', output_pdf))
        logging.info(f"PDF файл отправлен пользователю {user_id}")
    except Exception as e:
        bot.send_message(chat_id, f"Упс! Произошла ошибка при создании PDF: {e}")
        logging.exception(f"Ошибка создания PDF: {e}")

def process_media_group(group_id, chat_id, user_id):
    """Обработка собранной медиа-группы"""
    try:
        logging.info(f"Обработка группы {group_id} от пользователя {user_id}")
        images = []

        with media_group_lock:
            if group_id not in media_groups:
                return  # уже обработано или удалено
            messages = media_groups[group_id].copy()

        # Сортируем по message_id, чтобы сохранить порядок
        sorted_messages = sorted(messages, key=lambda x: x.message_id)

        for message in sorted_messages:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            img = Image.open(BytesIO(downloaded_file)).convert('RGB')
            images.append(img)

        create_pdf_from_images(images, chat_id, user_id)

    except Exception as e:
        bot.send_message(chat_id, f"Упс! Произошла ошибка при обработке альбома: {e}")
        logging.exception(f"Ошибка при обработке альбома: {e}")
    finally:
        # Очистка
        with media_group_lock:
            media_groups.pop(group_id, None)
            media_group_timers.pop(group_id, None)

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    group_id = message.media_group_id
    chat_id = message.chat.id
    user_id = message.from_user.id

    if group_id is None:
        # Обработка одиночного изображения
        try:
            logging.info(f"Получено одиночное изображение от пользователя {user_id}")
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            img = Image.open(BytesIO(downloaded_file)).convert('RGB')
            create_pdf_from_images([img], chat_id, user_id)
        except Exception as e:
            bot.send_message(chat_id, f"Не удалось обработать изображение: {e}")
            logging.exception(f"Ошибка при обработке одиночного фото: {e}")
    else:
        # Обработка альбома
        with media_group_lock:
            media_groups[group_id].append(message)

            # Отмена предыдущего таймера, если был
            if group_id in media_group_timers:
                timer = media_group_timers[group_id]
                timer.cancel()

            # Запуск нового таймера
            timer = threading.Timer(GROUP_TIMEOUT, process_media_group, args=(group_id, chat_id, user_id))
            timer.start()
            media_group_timers[group_id] = timer

if __name__ == '__main__':
    bot.polling(none_stop=True)
