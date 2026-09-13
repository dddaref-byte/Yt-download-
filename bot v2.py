"""
ربات تلگرامی دانلود از یوتیوب (نسخه‌ی چند کیفیتی)
---------------------------------------------------
کاربر لینک یوتیوب می‌فرستد، ربات لیست کیفیت‌های موجود ویدیو
(1080p/720p/480p/360p/240p/144p) به‌همراه حجم تقریبی هر کدام،
و دو گزینه صدا (128kbps / 320kbps) را به صورت دکمه نشان می‌دهد.
با انتخاب کاربر، همان کیفیت دانلود و برایش ارسال می‌شود.

پیش‌نیازها:
    pip install -r requirements.txt
    (ffmpeg باید روی سیستم نصب باشد)

اجرا:
    export BOT_TOKEN="توکن ربات شما از BotFather"
    python bot.py
"""

import os
import logging
import tempfile
import shutil
from pathlib import Path

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ----------------------------------------------------------------------
# تنظیمات
# ----------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# حداکثر حجمی که ربات‌های تلگرام از طریق Bot API استاندارد می‌توانند
# آپلود کنند (برای Local Bot API Server تا ۲ گیگ قابل افزایش است)
MAX_TELEGRAM_UPLOAD_MB = 50

# کیفیت‌های ویدیویی که می‌خواهیم به کاربر پیشنهاد بدهیم
VIDEO_HEIGHTS = [1080, 720, 480, 360, 240, 144]
# بیت‌ریت‌های صوتی که پیشنهاد می‌دهیم
AUDIO_BITRATES = [128, 320]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def resolve_ffmpeg_path():
    """
    اول دنبال ffmpeg نصب‌شده روی سیستم می‌گردد. اگر پیدا نشد (مثلاً روی
    هاست‌های ابری‌ای که دسترسی نصب پکیج سیستمی/apt نمی‌دهند)، از پکیج
    imageio-ffmpeg استفاده می‌کند که یک باینری ffmpeg مستقل را خودش
    دانلود و فراهم می‌کند و به دسترسی روت نیازی ندارد.
    """
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        logger.warning("ffmpeg پیدا نشد؛ تبدیل صدا/مرج ویدیو ممکن است کار نکند.")
        return None


FFMPEG_PATH = resolve_ffmpeg_path()


# ----------------------------------------------------------------------
# توابع کمکی
# ----------------------------------------------------------------------

def is_youtube_url(text: str) -> bool:
    text = text.lower()
    return ("youtube.com" in text) or ("youtu.be" in text)


def human_size(num_bytes) -> str:
    if not num_bytes:
        return "؟"
    num_bytes = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.0f}{unit}" if unit == "B" else f"{num_bytes:.1f}{unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f}TB"


def extract_info(url: str) -> dict:
    ydl_opts = {"quiet": True, "noplaylist": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def build_quality_buttons(info: dict):
    """
    بر اساس فرمت‌های واقعی موجود در info، دکمه‌های کیفیت ویدیو و صدا را می‌سازد.
    برای هر ارتفاع هدف، نزدیک‌ترین فرمت موجود ویدیو (فقط تصویر) پیدا می‌شود
    و حجم تخمینی = حجم آن فرمت + حجم بهترین فرمت صوتی (چون در نهایت مرج می‌شوند).
    """
    formats = info.get("formats", [])

    # بهترین فرمت صوتی جدا (برای تخمین حجم نهاییِ mp4 مرج‌شده)
    audio_formats = [
        f for f in formats
        if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    best_audio = max(
        audio_formats,
        key=lambda f: (f.get("abr") or 0),
        default=None,
    )
    best_audio_size = 0
    if best_audio:
        best_audio_size = best_audio.get("filesize") or best_audio.get("filesize_approx") or 0

    video_only = [
        f for f in formats
        if f.get("vcodec") != "none" and f.get("height")
    ]

    buttons = []
    seen_heights = set()
    for target_h in VIDEO_HEIGHTS:
        # نزدیک‌ترین کیفیت موجود به هدف را پیدا کن (ترجیحاً mp4)
        candidates = [f for f in video_only if f.get("height") == target_h]
        if not candidates:
            continue
        # ترجیح با mp4/avc برای سازگاری بیشتر با تلگرام
        candidates.sort(
            key=lambda f: (f.get("ext") != "mp4", -(f.get("tbr") or 0))
        )
        best = candidates[0]
        h = best.get("height")
        if h in seen_heights:
            continue
        seen_heights.add(h)

        vsize = best.get("filesize") or best.get("filesize_approx") or 0
        total_size = (vsize or 0) + (best_audio_size or 0)

        label = f"🎬 {h}p - {human_size(total_size)}"
        callback = f"v|{best['format_id']}"
        buttons.append((label, callback))

    audio_buttons = []
    for br in AUDIO_BITRATES:
        label = f"🎵 {br}kbps"
        callback = f"a|{br}"
        audio_buttons.append((label, callback))

    # چیدمان: هر کیفیت ویدیو یک ردیف، صداها در یک ردیف مشترک
    keyboard = [[InlineKeyboardButton(lbl, callback_data=cb)] for lbl, cb in buttons]
    if audio_buttons:
        keyboard.append(
            [InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in audio_buttons]
        )
    return keyboard


async def download_by_format_id(url: str, format_id: str, out_dir: str) -> Path:
    out_template = os.path.join(out_dir, "%(title).80s.%(ext)s")
    ydl_opts = {
        "format": f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio/best",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
    }
    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # وقتی مرج می‌شود پسوند نهایی mp4 خواهد بود
        filename = str(Path(filename).with_suffix(".mp4"))
    return Path(filename)


async def download_audio(url: str, bitrate: int, out_dir: str) -> Path:
    out_template = os.path.join(out_dir, "%(title).80s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(bitrate),
            }
        ],
        "noplaylist": True,
        "quiet": True,
    }
    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        filename = str(Path(filename).with_suffix(".mp3"))
    return Path(filename)


# ----------------------------------------------------------------------
# هندلرها
# ----------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "سلام! 👋\nلینک ویدیوی یوتیوب رو برام بفرست تا کیفیت‌های موجودشو نشونت بدم."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    if not is_youtube_url(text):
        await update.message.reply_text(
            "این یک لینک یوتیوب معتبر به نظر نمی‌رسه. لطفاً لینک ویدیو رو بفرست."
        )
        return

    status_msg = await update.message.reply_text("⏳ در حال بررسی لینک...")

    try:
        info = extract_info(text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("خطا در دریافت اطلاعات ویدیو")
        await status_msg.edit_text(f"❌ نتونستم اطلاعات این ویدیو رو بگیرم: {exc}")
        return

    # لینک و اطلاعات فرمت‌ها رو برای این چت ذخیره می‌کنیم
    context.user_data["last_url"] = text
    context.user_data["last_info"] = info

    keyboard = build_quality_buttons(info)
    if not keyboard:
        await status_msg.edit_text("❌ کیفیتی برای این ویدیو پیدا نشد.")
        return

    title = info.get("title", "ویدیو")
    duration = info.get("duration")
    duration_txt = f"{duration // 60}:{duration % 60:02d}" if duration else "؟"

    await status_msg.edit_text(
        f"🎬 {title}\n⏱ مدت: {duration_txt}\n\nکدوم کیفیت رو میخوای؟ 👇",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    url = context.user_data.get("last_url")
    if not url:
        await query.edit_message_text("لینکی پیدا نشد. لطفاً دوباره لینک رو بفرست.")
        return

    kind, value = query.data.split("|", 1)
    await query.edit_message_text("⏳ در حال دانلود... کمی صبر کن.")

    chat_id = query.message.chat_id
    await context.bot.send_chat_action(
        chat_id=chat_id,
        action=ChatAction.UPLOAD_VIDEO if kind == "v" else ChatAction.UPLOAD_VOICE,
    )

    tmp_dir = tempfile.mkdtemp(prefix="ytbot_")
    try:
        if kind == "v":
            file_path = await download_by_format_id(url, value, tmp_dir)
        else:
            file_path = await download_audio(url, int(value), tmp_dir)

        size_bytes = file_path.stat().st_size
        if size_bytes > MAX_TELEGRAM_UPLOAD_MB * 1024 * 1024:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ حجم فایل ({human_size(size_bytes)}) بیشتر از محدودیت "
                    f"آپلود تلگرام ({MAX_TELEGRAM_UPLOAD_MB}MB) است.\n"
                    "برای فایل‌های بزرگ‌تر باید از Local Bot API Server استفاده کنی."
                ),
            )
            return

        with open(file_path, "rb") as f:
            if kind == "v":
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=f,
                    caption=file_path.stem,
                    supports_streaming=True,
                    write_timeout=180,
                    read_timeout=180,
                )
            else:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=f,
                    title=file_path.stem,
                    write_timeout=180,
                    read_timeout=180,
                )

    except Exception as exc:  # noqa: BLE001
        logger.exception("خطا در دانلود/ارسال فایل")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ مشکلی پیش اومد: {exc}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ----------------------------------------------------------------------
# اجرای ربات
# ----------------------------------------------------------------------

def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit(
            "متغیر محیطی BOT_TOKEN تنظیم نشده. توکن ربات رو از BotFather بگیر و تنظیمش کن."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_button))

    logger.info("ربات در حال اجراست...")
    app.run_polling()


if __name__ == "__main__":
    main()
