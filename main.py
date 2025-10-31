import os
import re
import json
import time
import asyncio
import shutil
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote

import yt_dlp
from telegram import Update, InputMediaVideo, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackContext, CallbackQueryHandler
)
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# === TOKEN ===
TOKEN = os.environ.get("BOT_TOKEN", "7847208260:AAG4XeEcE1RLixadm3YZGKvC2fqv4_NvFuc")

# === YouTube ===
FORMAT_EMOJIS = {'144': 'Mobile', '360': 'TV', '480': 'Film', '720': 'HD', '1080': 'Full HD', 'mp3': 'Music'}
MAX_FILE_SIZE = 30 * 1024 * 1024
DOWNLOAD_TIMEOUT = 300

# === Papkalar ===
downloads_dir = os.path.join(os.getcwd(), "downloads")
os.makedirs(downloads_dir, exist_ok=True)
user_data = {}

# === Tozalash ===
async def clean_downloads_folder():
    try:
        for f in os.listdir(downloads_dir):
            path = os.path.join(downloads_dir, f)
            if os.path.isfile(path): os.unlink(path)
            elif os.path.isdir(path): shutil.rmtree(path)
    except Exception as e:
        print(f"Tozalash xatosi: {e}")

# === Instagram: Public postni JSON orqali olish (LOGINsiz) ===
def get_instagram_media_json(shortcode):
    url = f"https://www.instagram.com/p/{shortcode}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.instagram.com/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        script = soup.find("script", text=re.compile("window\._sharedData"))
        if not script:
            return None

        json_text = re.search(r"window\._sharedData = (\{.+?\});", script.string)
        if not json_text:
            return None

        data = json.loads(json_text.group(1))
        media = data["entry_data"]["PostPage"][0]["graphql"]["shortcode_media"]

        return media
    except Exception as e:
        print(f"Instagram JSON olish xatosi: {e}")
        return None

# === Instagram media yuklash (LOGINsiz) ===
async def download_instagram_media(shortcode):
    await clean_downloads_folder()
    media_files = []

    try:
        media = get_instagram_media_json(shortcode)
        if not media:
            return []

        is_video = media.get("is_video", False)
        video_url = media.get("video_url")
        display_url = media.get("display_url")

        # Single post
        if is_video and video_url:
            fn = os.path.join(downloads_dir, "ig_video.mp4")
            with requests.get(video_url, stream=True, timeout=60) as r:
                with open(fn, 'wb') as f:
                    for chunk in r.iter_content(1024*1024):
                        f.write(chunk)
            if os.path.getsize(fn) <= MAX_FILE_SIZE:
                media_files.append(('video', fn))
            else:
                os.remove(fn)

        elif display_url:
            fn = os.path.join(downloads_dir, "ig_image.jpg")
            with open(fn, 'wb') as f:
                f.write(requests.get(display_url, timeout=60).content)
            if os.path.getsize(fn) <= MAX_FILE_SIZE:
                media_files.append(('image', fn))
            else:
                os.remove(fn)

        # Carousel (Sidecar)
        if media.get("edge_sidecar_to_children"):
            nodes = media["edge_sidecar_to_children"]["edges"]
            for i, node in enumerate(nodes[:10]):
                node = node["node"]
                if node["is_video"]:
                    url = node["video_url"]
                    fn = os.path.join(downloads_dir, f"ig_video_{i}.mp4")
                else:
                    url = node["display_url"]
                    fn = os.path.join(downloads_dir, f"ig_image_{i}.jpg")

                with requests.get(url, stream=True, timeout=60) as r:
                    with open(fn, 'wb') as f:
                        for chunk in r.iter_content(1024*1024):
                            f.write(chunk)

                if os.path.getsize(fn) <= MAX_FILE_SIZE:
                    media_files.append(('video' if node["is_video"] else 'image', fn))
                else:
                    os.remove(fn)

    except Exception as e:
        print(f"Instagram yuklash xatosi: {e}")

    return media_files

# === YouTube formatlari ===
def get_available_formats(info):
    formats = {'video': {}, 'audio': {}}
    for f in info.get('formats', []):
        if not isinstance(f, dict): continue
        if f.get('vcodec') != 'none' and f.get('ext') == 'mp4':
            h = f.get('height')
            size = f.get('filesize', 0)
            if h and size and size <= MAX_FILE_SIZE:
                hs = str(h)
                if hs not in formats['video'] or size < formats['video'][hs].get('filesize', float('inf')):
                    formats['video'][hs] = {'format_id': f['format_id'], 'filesize': size}
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            ext = f.get('ext')
            size = f.get('filesize', 0)
            if ext in ['m4a', 'mp3'] and size and size <= MAX_FILE_SIZE:
                if ext not in formats['audio'] or (f.get('abr', 0) > formats['audio'][ext].get('abr', 0)):
                    formats['audio'][ext] = {'format_id': f['format_id'], 'filesize': size}
    return formats

# === Format tugmalari ===
async def send_format_buttons(chat_id, info, bot):
    formats = user_data[chat_id]["formats"]
    buttons = []
    for h, f in sorted(formats['video'].items(), key=lambda x: int(x[0])):
        size_mb = f['filesize'] / (1024 * 1024)
        buttons.append(InlineKeyboardButton(
            f"{FORMAT_EMOJIS.get(h, 'Film')} {h}p | {size_mb:.1f}MB",
            callback_data=f"video_{f['format_id']}"
        ))
    for ext, f in formats['audio'].items():
        size_mb = f['filesize'] / (1024 * 1024)
        buttons.append(InlineKeyboardButton(
            f"{FORMAT_EMOJIS.get(ext, 'Music')} {ext.upper()} | {size_mb:.1f}MB",
            callback_data=f"audio_{f['format_id']}"
        ))
    if not buttons:
        await bot.send_message(chat_id, "30MB dan kichik format topilmadi.")
        return
    keyboard = [buttons[i:i+2] for i in range(0, len(buttons), 2)]
    markup = InlineKeyboardMarkup(keyboard)
    caption = f"<b>{info.get('title', 'Video')}</b>\n\nFormatni tanlang:"
    thumbnail = info.get('thumbnail')
    try:
        msg = await bot.send_photo(chat_id, thumbnail, caption=caption, parse_mode='HTML', reply_markup=markup) if thumbnail else \
               await bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
        user_data[chat_id]["format_message_id"] = msg.message_id
    except Exception as e:
        await bot.send_message(chat_id, f"Xatolik: {e}")

# === Yuklash progressi ===
async def progress_hook(d, chat_id, msg_id, bot):
    if d.get('status') == 'downloading':
        try:
            text = f"Yuklanmoqda... {d.get('_percent_str', '0%')}\n{d.get('_speed_str', 'N/A')} | {d.get('_eta_str', 'N/A')}"
            await bot.edit_message_text(text, chat_id, msg_id)
        except: pass

# === Media yuklash ===
async def download_media(chat_id, format_id, is_video, bot):
    await clean_downloads_folder()
    try:
        result = await asyncio.wait_for(_download_media(chat_id, format_id, is_video, bot), timeout=DOWNLOAD_TIMEOUT)
        return result
    except asyncio.TimeoutError:
        await bot.send_message(chat_id, "Yuklash vaqti tugadi.")
    except Exception as e:
        await bot.send_message(chat_id, f"Yuklash xatosi: {e}")
    return None

async def _download_media(chat_id, format_id, is_video, bot):
    url = user_data[chat_id]["url"]
    ext = "mp4" if is_video else "mp3"
    filename = os.path.join(downloads_dir, f"{chat_id}_{int(time.time())}.{ext}")
    msg = await bot.send_message(chat_id, "Yuklanmoqda...")
    await bot.send_chat_action(chat_id, 'upload_video' if is_video else 'upload_audio')

    ydl_opts = {
        'format': f'{format_id}+bestaudio/best' if is_video else 'bestaudio/best',
        'outtmpl': filename,
        'quiet': True,
        'merge_output_format': 'mp4',
        'socket_timeout': 120,
        'retries': 10,
        'noprogress': False,
    }
    if is_video:
        ydl_opts['postprocessors'] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]
    else:
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

    def progress(d):
        if d['status'] == 'downloading':
            asyncio.create_task(progress_hook(d, chat_id, msg.message_id, bot))
    ydl_opts['progress_hooks'] = [progress]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        if not is_video and os.path.exists(f"{filename}.mp3"):
            os.rename(f"{filename}.mp3", filename)
        if not os.path.exists(filename):
            raise Exception("Fayl yaratilmadi")
        if os.path.getsize(filename) > MAX_FILE_SIZE:
            os.remove(filename)
            raise Exception("Fayl 30MB dan katta")
        return filename, msg.message_id
    except Exception as e:
        await bot.edit_message_text(f"Yuklash xatosi: {e}", chat_id, msg.message_id)
        for f in [filename, f"{filename}.mp3"]:
            if os.path.exists(f): os.remove(f)
        return None

# === Handlers ===
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "*Media Yuklovchi Bot*\n\n"
        "YouTube yoki Instagram linkini yuboring.\n"
        "30MB gacha fayllar yuklanadi.\n\n"
        "*Misollar:*\n"
        "`https://youtu.be/dQw4w9WgXcQ`\n"
        "`https://instagram.com/p/ABC123/`",
        parse_mode='Markdown'
    )

async def handle_youtube(update: Update, context: CallbackContext):
    chat_id = update.message.chat.id
    url = update.message.text.strip()
    if not url.startswith(('http://', 'https://')): url = 'https://' + url
    await clean_downloads_folder()
    msg = await context.bot.send_message(chat_id, "Video ma'lumotlari olinmoqda...")
    try:
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
        user_data[chat_id] = {"url": url, "info": info, "formats": get_available_formats(info)}
        await msg.delete()
        await send_format_buttons(chat_id, info, context.bot)
    except Exception as e:
        await context.bot.send_message(chat_id, f"Xatolik: {e}")

async def handle_instagram(update: Update, context: CallbackContext):
    url = update.message.text.strip()
    try:
        shortcode = re.search(r'instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)', url)
        if not shortcode:
            await update.message.reply_text("Noto'g'ri Instagram link.")
            return
        shortcode = shortcode.group(1)

        await clean_downloads_folder()
        msg = await update.message.reply_text("Instagram media yuklanmoqda...")

        media_files = await download_instagram_media(shortcode)
        await msg.delete()

        if not media_files:
            await update.message.reply_text("Media topilmadi yoki hajmi katta.")
            return

        caption = "Yuklab olindi: @dilxush_bahodirov"

        if len(media_files) == 1:
            typ, path = media_files[0]
            if typ == 'video':
                await update.message.reply_video(open(path, 'rb'), caption=caption)
            else:
                await update.message.reply_photo(open(path, 'rb'), caption=caption)
            os.remove(path)
        else:
            group = []
            for i, (typ, path) in enumerate(media_files):
                if typ == 'video':
                    if i == 0:
                        await update.message.reply_video(open(path, 'rb'), caption=caption)
                    else:
                        group.append(InputMediaVideo(open(path, 'rb'), caption=caption if i == len(media_files)-1 else None))
                else:
                    group.append(InputMediaPhoto(open(path, 'rb'), caption=caption if i == len(media_files)-1 else None))
                os.remove(path)
            if group:
                await update.message.reply_media_group(group)

    except Exception as e:
        await update.message.reply_text(f"Xatolik: {e}")

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text or ''
    if 'youtube.com' in text or 'youtu.be' in text:
        await handle_youtube(update, context)
    elif 'instagram.com' in text:
        await handle_instagram(update, context)
    else:
        await update.message.reply_text("YouTube yoki Instagram linkini yuboring.")

async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat.id
    data = query.data
    if chat_id not in user_data:
        await query.edit_message_text("Sessiya tugadi.")
        return
    try:
        if "format_message_id" in user_data[chat_id]:
            await context.bot.delete_message(chat_id, user_data[chat_id]["format_message_id"])
        is_video = data.startswith("video_")
        format_id = data.split("_", 1)[1]
        result = await download_media(chat_id, format_id, is_video, context.bot)
        if not result: return
        file_path, msg_id = result
        title = user_data[chat_id]['info'].get('title', 'Media')
        caption = f"{title}\n@dilxush_bahodirov"
        if is_video:
            await context.bot.send_video(chat_id, open(file_path, 'rb'), caption=caption, parse_mode='HTML')
        else:
            await context.bot.send_audio(chat_id, open(file_path, 'rb'), title=title, performer="YouTube", caption=caption, parse_mode='HTML')
        await context.bot.delete_message(chat_id, msg_id)
        os.remove(file_path)
        await clean_downloads_folder()
    except Exception as e:
        await context.bot.send_message(chat_id, f"Xatolik: {e}")

# === Main ===
def main():
    PORT = int(os.environ.get("PORT", 8443))
    APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://instasave-1-cbdc.onrender.com")
    app = Application.builder().token(TOKEN).read_timeout(100).write_timeout(100).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot ishga tushdi...")
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"{APP_URL}/{TOKEN}")

if __name__ == "__main__":
    main()
