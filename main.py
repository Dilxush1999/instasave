import os
import requests
import instaloader
import yt_dlp
import time
import asyncio
import shutil
from telegram import Update, InputMediaVideo, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

TOKEN = '7847208260:AAG4XeEcE1RLixadm3YZGKvC2fqv4_NvFuc'

# YouTube related variables and functions
FORMAT_EMOJIS = {
    '144': '📱', '360': '📺', '480': '🎬',
    '720': '📹', '1080': '🎥', 'mp3': '🎵', 'aac': '🎵'
}

MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB limit
DOWNLOAD_TIMEOUT = 300  # 5 daqiqa

# downloads papkasini yaratish
downloads_dir = os.path.join(os.getcwd(), "downloads")
os.makedirs(downloads_dir, exist_ok=True)

user_data = {}

async def clean_downloads_folder():
    """Downloads papkasidagi barcha fayllarni tozalash"""
    try:
        for filename in os.listdir(downloads_dir):
            file_path = os.path.join(downloads_dir, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f"Faylni o'chirishda xatolik {file_path}: {e}")
    except Exception as e:
        print(f"Downloads papkasini tozalashda xatolik: {e}")

async def progress_hook(d, chat_id, message_id, bot):
    try:
        if isinstance(d, dict) and d.get('status') == 'downloading':
            percent = d.get('_percent_str', '0%')
            speed = d.get('_speed_str', 'N/A')
            eta = d.get('_eta_str', 'N/A')
            
            progress_text = f"⬇️ Yuklanmoqda... {percent}\n⚡ {speed} | ⏳ {eta}"
            
            await bot.edit_message_text(
                progress_text,
                chat_id=chat_id,
                message_id=message_id
            )
    except Exception as e:
        print(f"Progress xatosi: {e}")

def get_available_formats(info):
    formats = {'video': {}, 'audio': {}}
    
    for f in info.get('formats', []):
        if not isinstance(f, dict):
            continue
            
        # Video formatlari (faqat 50MB dan kichik va filesize mavjud bo'lganlar)
        if f.get('vcodec') != 'none' and f.get('ext') == 'mp4':
            height = f.get('height')
            filesize = f.get('filesize', 0)
            # Faqat filesize mavjud va 50MB dan kichik bo'lgan formatlar
            if height and filesize and filesize <= MAX_FILE_SIZE:
                height_str = str(height)
                if height_str not in formats['video'] or filesize > formats['video'][height_str].get('filesize', 0):
                    formats['video'][height_str] = {
                        'format_id': f['format_id'],
                        'filesize': filesize,
                        'url': f.get('url')
                    }
        
        # Audio formatlari (faqat 50MB dan kichik va filesize mavjud bo'lganlar)
        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
            ext = f.get('ext')
            filesize = f.get('filesize', 0)
            if ext in ['m4a', 'mp3'] and filesize and filesize <= MAX_FILE_SIZE:
                if ext not in formats['audio'] or (f.get('abr', 0) > formats['audio'][ext].get('abr', 0)):
                    formats['audio'][ext] = {
                        'format_id': f['format_id'],
                        'filesize': filesize,
                        'url': f.get('url')
                    }
                    
    return formats

async def send_format_buttons(chat_id, info, bot):
    formats = user_data[chat_id]["formats"]
    buttons = []
    
    # Video formatlari uchun tugmalar (faqat filesize mavjud va 50MB dan kichiklar)
    for height, f in sorted(formats['video'].items(), key=lambda x: int(x[0])):
        size = f.get('filesize', 0) / (1024 * 1024)
        buttons.append(InlineKeyboardButton(
            f"{FORMAT_EMOJIS.get(height, '🎬')} {height}p | {size:.1f}MB",
            callback_data=f"video_{f['format_id']}"
        ))
    
    # Audio formatlari uchun tugmalar (faqat filesize mavjud va 50MB dan kichiklar)
    for ext, f in formats['audio'].items():
        size = f.get('filesize', 0) / (1024 * 1024)
        buttons.append(InlineKeyboardButton(
            f"{FORMAT_EMOJIS.get(ext, '🎵')} {ext.upper()} | {size:.1f}MB",
            callback_data=f"audio_{f['format_id']}"
        ))
    
    if not buttons:
        await bot.send_message(chat_id, "❌ Ushbu videoda yuklab olinadigan formatlar topilmadi (faqat 50MB dan kichik va hajmi ma'lum formatlar ko'rsatiladi).")
        return
    
    # Tugmalarni qatorlarga ajratish (har bir qatorda 2 ta tugma)
    keyboard = []
    for i in range(0, len(buttons), 2):
        row = buttons[i:i+2]
        keyboard.append(row)
    
    markup = InlineKeyboardMarkup(keyboard)
    
    caption = f"🎬 <b>{info.get('title', 'Video')}</b>\n\nQuyidagi formatlardan birini tanlang (faqat 50MB dan kichik va hajmi ma'lum formatlar ko'rsatiladi):"
    
    try:
        # Video rasmini yoki standart xabar yuborish
        thumbnail = info.get('thumbnail')
        if thumbnail:
            msg = await bot.send_photo(
                chat_id, thumbnail,
                caption=caption,
                parse_mode='HTML',
                reply_markup=markup
            )
        else:
            msg = await bot.send_message(
                chat_id, caption,
                parse_mode='HTML',
                reply_markup=markup
            )
            
        user_data[chat_id]["format_message_id"] = msg.message_id
        
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Xatolik: {str(e)}")

async def download_media(chat_id, format_id, is_video=True, bot=None):
    try:
        # Avval downloads papkasini tozalaymiz
        await clean_downloads_folder()
        
        result = await asyncio.wait_for(_download_media(chat_id, format_id, is_video, bot), timeout=DOWNLOAD_TIMEOUT)
        return result
    except asyncio.TimeoutError:
        await bot.send_message(chat_id, "❌ Yuklash juda uzoq davom etdi. Iltimos, qayta urinib ko'ring.")
        return None
    except Exception as e:
        await bot.send_message(chat_id, f"❌ Yuklashda xatolik: {str(e)}")
        return None

async def _download_media(chat_id, format_id, is_video, bot):
    url = user_data[chat_id]["url"]
    ext = "mp4" if is_video else "mp3"
    
    # Unique fayl nomi yaratish
    timestamp = int(time.time())
    filename = os.path.join(downloads_dir, f"{chat_id}_{timestamp}.{ext}")
    
    loading_msg = await bot.send_message(chat_id, "⏳ Yuklanmoqda, iltimos kuting...")
    
    try:
        await bot.send_chat_action(chat_id, 'upload_video' if is_video else 'upload_audio')
        
        ydl_opts = {
            'format': f'{format_id}+bestaudio/best' if is_video else 'bestaudio/best',
            'outtmpl': filename,
            'quiet': True,
            'merge_output_format': 'mp4',
            'socket_timeout': 120,
            'retries': 10,
            'fragment_retries': 10,
            'extractor_retries': 3,
            'noprogress': False,
            'postprocessors': [],
            'extractaudio': not is_video,
            'keepvideo': is_video,
        }

        if is_video:
            ydl_opts['postprocessors'].append({
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            })
        else:
            ydl_opts['postprocessors'].append({
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            })

        progress_messages = {'message_id': loading_msg.message_id}

        def progress_callback(d):
            if d['status'] == 'downloading':
                asyncio.create_task(progress_hook(d, chat_id, progress_messages['message_id'], bot))
        
        ydl_opts['progress_hooks'] = [progress_callback]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Audio fayllar uchun qo'shimcha tekshirish
        if not is_video:
            # Agar .mp3.mp3 yaratilgan bo'lsa, uni to'g'irlaymiz
            double_ext = f"{filename}.mp3"
            if os.path.exists(double_ext):
                os.rename(double_ext, filename)
        
        if not os.path.exists(filename):
            raise Exception("Fayl yaratilmadi yoki yo'qolgan")
            
        file_size = os.path.getsize(filename)
        if file_size > MAX_FILE_SIZE:
            os.remove(filename)
            raise Exception("Fayl hajmi 50MB dan katta")
        
        return filename, progress_messages['message_id']
    
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Yuklashda xatolik: {str(e)}",
            chat_id=chat_id,
            message_id=loading_msg.message_id
        )
        # Har qanday fayllarni o'chiramiz
        for f in [filename, f"{filename}.mp3"]:
            if os.path.exists(f):
                os.remove(f)
        return None

# Instagram related functions
async def download_instagram_media(shortcode):
    L = instaloader.Instaloader()
    post = instaloader.Post.from_shortcode(L.context, shortcode)
    
    media_files = []
    
    if post.typename == 'GraphSidecar':
        for index, node in enumerate(post.get_sidecar_nodes()):
            if index >= 10:
                break
            if node.is_video:
                filename = os.path.join(downloads_dir, f"video_{index}.mp4")
                with requests.get(node.video_url, stream=True, timeout=60) as r:
                    with open(filename, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=1024*1024):
                            if chunk:
                                f.write(chunk)
                # Fayl hajmini tekshiramiz
                if os.path.exists(filename) and os.path.getsize(filename) <= MAX_FILE_SIZE:
                    media_files.append(('video', filename))
                else:
                    if os.path.exists(filename):
                        os.remove(filename)
            else:
                filename = os.path.join(downloads_dir, f"image_{index}.jpg")
                with open(filename, 'wb') as f:
                    f.write(requests.get(node.display_url, timeout=60).content)
                # Fayl hajmini tekshiramiz
                if os.path.exists(filename) and os.path.getsize(filename) <= MAX_FILE_SIZE:
                    media_files.append(('image', filename))
                else:
                    if os.path.exists(filename):
                        os.remove(filename)
    
    elif post.typename == 'GraphVideo':
        filename = os.path.join(downloads_dir, "video.mp4")
        with requests.get(post.video_url, stream=True, timeout=60) as r:
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
        # Fayl hajmini tekshiramiz
        if os.path.exists(filename) and os.path.getsize(filename) <= MAX_FILE_SIZE:
            media_files.append(('video', filename))
        else:
            if os.path.exists(filename):
                os.remove(filename)
    
    elif post.typename == 'GraphImage':
        filename = os.path.join(downloads_dir, "image.jpg")
        with open(filename, 'wb') as f:
            f.write(requests.get(post.url, timeout=60).content)
        # Fayl hajmini tekshiramiz
        if os.path.exists(filename) and os.path.getsize(filename) <= MAX_FILE_SIZE:
            media_files.append(('image', filename))
        else:
            if os.path.exists(filename):
                os.remove(filename)
    
    return media_files

# Bot handlers
async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "📸 *Media Yuklovchi Bot* 📹\n\n"
        "YouTube yoki Instagramdagi videolar va rasmlarni yuklab olish uchun linkini yuboring.\n"
        "⚠️ Eslatma: Faqat 50MB dan kichik fayllar yuklanadi.\n\n"
        "🌐 *Namunalar:*\n"
        "YouTube: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`\n"
        "Instagram: `https://www.instagram.com/p/Cz6ZQKjN3qP/`\n\n"
        "Bot sizga media fayllarni yuklab beradi.",
        parse_mode='Markdown'
    )

async def handle_youtube_link(update: Update, context: CallbackContext):
    chat_id = update.message.chat.id
    url = update.message.text.strip()
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        # Avval downloads papkasini tozalaymiz
        await clean_downloads_folder()
        
        loading_msg = await context.bot.send_message(chat_id, "🔍 Video ma'lumotlari olinmoqda...")
        
        ydl_opts = {
            'quiet': True,
            'extract_flat': False,
            'force_generic_extractor': True,
            'socket_timeout': 60,
            'retries': 5
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise Exception("Video ma'lumotlari olinmadi")
            
            user_data[chat_id] = {
                "url": url,
                "info": info,
                "formats": get_available_formats(info)
            }
            
            await loading_msg.delete()
            await send_format_buttons(chat_id, info, context.bot)
            
    except Exception as e:
        error_msg = f"❌ Xatolik: {str(e)}"
        await context.bot.send_message(chat_id, error_msg)

async def handle_instagram_link(update: Update, context: CallbackContext):
    try:
        # Avval downloads papkasini tozalaymiz
        await clean_downloads_folder()
        
        loading_msg = await update.message.reply_text("⏳ Media yuklanmoqda...")
        
        shortcode = update.message.text.split('/')[-2]
        media_files = await download_instagram_media(shortcode)
        
        if not media_files:
            await loading_msg.edit_text("❌ Ushbu postda media topilmadi yoki fayl hajmi 50MB dan katta.")
            return
        
        await loading_msg.delete()
        
        caption = "🎬 Yuklab olindi: @dilxush_bahodirov"
        
        if len(media_files) == 1:
            media_type, filename = media_files[0]
            if media_type == 'video':
                await update.message.reply_video(video=open(filename, 'rb'), caption=caption)
            else:
                await update.message.reply_photo(photo=open(filename, 'rb'), caption=caption)
            
            # Faylni o'chiramiz
            try:
                os.remove(filename)
            except:
                pass
        else:
            media_group = []
            for i, (media_type, filename) in enumerate(media_files):
                if media_type == 'video':
                    if i == 0:
                        await update.message.reply_video(video=open(filename, 'rb'), caption=caption)
                    else:
                        media_group.append(InputMediaVideo(media=open(filename, 'rb'), 
                                                     caption=caption if i == len(media_files)-1 else None))
                else:
                    media_group.append(InputMediaPhoto(media=open(filename, 'rb'), 
                                                caption=caption if i == len(media_files)-1 else None))
                
                # Faylni o'chiramiz
                try:
                    os.remove(filename)
                except:
                    pass
            
            if media_group:
                await update.message.reply_media_group(media=media_group)
            
    except Exception as e:
        error_msg = await update.message.reply_text(f"❌ Xatolik yuz berdi: {str(e)}")
        await asyncio.sleep(10)
        await error_msg.delete()

async def handle_message(update: Update, context: CallbackContext) -> None:
    text = update.message.text or ''
    
    if 'youtube.com' in text or 'youtu.be' in text:
        await handle_youtube_link(update, context)
    elif 'instagram.com' in text:
        await handle_instagram_link(update, context)
    else:
        await update.message.reply_text("❌ Iltimos, YouTube yoki Instagram linkini yuboring.")

async def handle_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat.id
    data = query.data
    
    if chat_id not in user_data:
        await query.edit_message_text("❌ Session muddati tugadi. Iltimos, linkni qayta yuboring.")
        return
    
    try:
        if "format_message_id" in user_data[chat_id]:
            try:
                await context.bot.delete_message(chat_id, user_data[chat_id]["format_message_id"])
            except:
                pass
        
        is_video = data.startswith("video_")
        format_id = data[6:]
        
        result = await download_media(chat_id, format_id, is_video, context.bot)
        if not result:
            return
            
        file_path, progress_msg_id = result
        
        caption = f"🎬 {user_data[chat_id]['info'].get('title', 'Video')}\n🔗 @dilxush_bahodirov"
        
        try:
            if is_video:
                with open(file_path, 'rb') as video_file:
                    await context.bot.send_video(
                        chat_id, video_file,
                        caption=caption,
                        parse_mode='HTML'
                    )
            else:
                with open(file_path, 'rb') as audio_file:
                    await context.bot.send_audio(
                        chat_id, audio_file,
                        caption=caption,
                        parse_mode='HTML',
                        title=user_data[chat_id]['info'].get('title', 'Audio'),
                        performer="YouTube"
                    )
        except Exception as e:
            await context.bot.send_message(chat_id, f"❌ Media yuborishda xatolik: {str(e)}")
            raise
        
        # Progress xabarini o'chiramiz
        try:
            await context.bot.delete_message(chat_id, progress_msg_id)
        except:
            pass
        
        # Faylni o'chiramiz
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass
            
        # Downloads papkasini tozalaymiz
        await clean_downloads_folder()
            
    except Exception as e:
        error_msg = f"❌ Yuklashda xatolik: {str(e)}"
        await context.bot.send_message(chat_id, error_msg)

def main():
    PORT = int(os.environ.get("PORT", 8443))
    APP_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://instasave-1-cbdc.onrender.com")

    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    print("✅ Bot server ishga tushdi...")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{APP_URL}/{TOKEN}"
    )

if __name__ == "__main__":
    main()



