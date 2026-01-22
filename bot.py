#!/usr/bin/env python3
"""
Telegram Video Downloader Bot - Render.com Deployment
Simplified version that works with PTB v20+
"""

import os
import sys
import logging
import tempfile
import asyncio
import math
import time
import traceback
import re
from typing import Optional
from pathlib import Path
import shutil
from urllib.parse import urlparse

import yt_dlp
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ============ CONFIGURATION ============
# Get bot token from environment
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable is not set!")
    print("Please set it in Render's Environment settings.")
    sys.exit(1)

# Settings for Render Free Tier
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500MB
MAX_VIDEO_DURATION = 1800  # 30 minutes (safer for free tier)
MAX_CONCURRENT_DOWNLOADS = 1

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Reduce noise
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

# ============ BOT CLASS ============
class VideoDownloaderBot:
    def __init__(self):
        self.active_downloads = {}
        self.download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        
        # Platform detection
        self.supported_platforms = {
            'youtube': [r'youtube\.com', r'youtu\.be'],
            'tiktok': [r'tiktok\.com'],
            'instagram': [r'instagram\.com'],
            'twitter': [r'twitter\.com', r'x\.com'],
            'facebook': [r'facebook\.com'],
        }
        
        # yt-dlp options
        self.ydl_opts = {
            'format': 'best[height<=720]/best',
            'outtmpl': '%(title).100s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'retries': 3,
            'fragment_retries': 3,
            'socket_timeout': 30,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            },
        }
        
        # Temp directory
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_bot_"))
        logger.info(f"Bot initialized. Temp dir: {self.temp_dir}")
    
    # ============ HELPER METHODS ============
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes == 0:
            return "0B"
        size_name = ("B", "KB", "MB", "GB")
        i = int(math.floor(math.log(size_bytes, 1024)))
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"
    
    @staticmethod
    def _format_duration(seconds: int) -> str:
        """Format duration in human readable format."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    
    def _detect_platform(self, url: str) -> Optional[str]:
        """Detect which platform the URL belongs to."""
        try:
            domain = urlparse(url).netloc.lower()
            for platform, patterns in self.supported_platforms.items():
                for pattern in patterns:
                    if re.search(pattern, domain, re.IGNORECASE):
                        return platform
            return None
        except Exception:
            return None
    
    @staticmethod
    def _extract_url(text: str) -> Optional[str]:
        """Extract URL from text."""
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        matches = re.findall(url_pattern, text)
        if matches:
            url = matches[0]
            if not url.startswith('http'):
                url = 'https://' + url
            return url
        return None
    
    # ============ COMMAND HANDLERS ============
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        
        welcome_text = f"""
👋 *Welcome {user.first_name}!*

🎬 *Video Downloader Bot*
⚡ *Deployed on Render.com*

📥 *Supported Platforms:*
• YouTube, TikTok, Instagram
• Twitter/X, Facebook

🚀 *How to use:* Just send any video link!

⚠️ *Limits (Render Free Tier):*
• Max file: 500MB
• Max duration: 30 minutes
• Only 1 download at a time

💡 *Tip:* Use `-fast` for faster downloads

Made with ❤️ - Running on Render
        """
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
        logger.info(f"User {user.id} started the bot")
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        help_text = """
🤖 *Bot Commands:*
/start - Start the bot
/help - Show this help
/stats - Bot statistics
/cancel - Cancel download

🎯 *Quality Flags:*
-fast - Faster download (480p)
-audio - Audio only (MP3)

⚠️ *Limits:*
• Max file: 500MB
• Max duration: 30 minutes
• Only 1 download at a time

💡 *Tip:* For best results, use short videos
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command."""
        stats_text = f"""
📊 *Bot Statistics*

📥 *Active Downloads:* {len(self.active_downloads)}

🏓 *Status:* ✅ Online on Render

🔄 *Restart Bot:* If bot stops responding, wait 1 minute and try again.
        """
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    
    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /cancel command."""
        user_id = update.effective_user.id
        
        if user_id in self.active_downloads:
            task = self.active_downloads[user_id]
            if not task.done():
                task.cancel()
                await update.message.reply_text("✅ Download cancelled!")
                logger.info(f"User {user_id} cancelled download")
            else:
                await update.message.reply_text("⚠️ No active download.")
        else:
            await update.message.reply_text("⚠️ No active download.")
    
    # ============ MESSAGE HANDLER ============
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages with URLs."""
        user = update.effective_user
        message_text = update.message.text.strip()
        
        # Extract URL
        url = self._extract_url(message_text)
        if not url:
            await update.message.reply_text("Please send a valid video URL.")
            return
        
        # Detect platform
        platform = self._detect_platform(url)
        if not platform:
            await update.message.reply_text(
                "❌ Unsupported platform!\n"
                "Supported: YouTube, TikTok, Instagram, Twitter, Facebook"
            )
            return
        
        # Check quality flags
        quality = "best"
        if "-fast" in message_text.lower():
            quality = "fast"
        elif "-audio" in message_text.lower():
            quality = "audio"
        
        # Check active downloads
        if user.id in self.active_downloads:
            task = self.active_downloads[user.id]
            if not task.done():
                await update.message.reply_text(
                    "⚠️ You have a download in progress!\n"
                    "Please wait or use /cancel to cancel it first."
                )
                return
        
        # Process download
        await self._process_download(update, url, platform, quality)
    
    async def _process_download(self, update: Update, url: str, platform: str, quality: str):
        """Process download request."""
        user = update.effective_user
        
        # Create status message
        status_msg = await update.message.reply_text(
            f"🔍 *Processing {platform} link...*\n"
            f"⏳ Please wait...",
            parse_mode='Markdown'
        )
        
        try:
            # Get video info
            info = await self._get_video_info(url)
            if not info:
                await status_msg.edit_text(
                    "❌ Could not fetch video information.\n"
                    "URL might be invalid or private."
                )
                return
            
            # Check duration
            duration = info.get('duration', 0)
            if duration > MAX_VIDEO_DURATION:
                await status_msg.edit_text(
                    f"❌ Video too long!\n"
                    f"Duration: {self._format_duration(duration)}\n"
                    f"Max: {self._format_duration(MAX_VIDEO_DURATION)}"
                )
                return
            
            # Start download task
            task = asyncio.create_task(
                self._download_video(update, url, platform, quality, status_msg, info)
            )
            self.active_downloads[user.id] = task
            
            # Cleanup callback
            def cleanup(fut):
                if user.id in self.active_downloads:
                    del self.active_downloads[user.id]
            
            task.add_done_callback(cleanup)
            
        except Exception as e:
            logger.error(f"Process error: {e}")
            await status_msg.edit_text("❌ Error processing request.")
    
    async def _get_video_info(self, url: str) -> Optional[dict]:
        """Get video information."""
        try:
            loop = asyncio.get_event_loop()
            
            def extract_info():
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            info = await asyncio.wait_for(loop.run_in_executor(None, extract_info), timeout=30)
            return info
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout extracting info: {url}")
            return None
        except Exception as e:
            logger.error(f"Info error: {e}")
            return None
    
    async def _download_video(self, update: Update, url: str, platform: str, quality: str,
                             status_msg, info: dict):
        """Download and send video."""
        user = update.effective_user
        
        try:
            # Update status
            title = info.get('title', 'Video')[:50]
            await status_msg.edit_text(
                f"⬇️ *Downloading...*\n"
                f"📹 *{title}*\n"
                f"🌐 Platform: {platform}\n"
                f"⏳ Please wait...",
                parse_mode='Markdown'
            )
            
            # Create temp directory
            with tempfile.TemporaryDirectory(dir=self.temp_dir) as temp_dir:
                temp_path = Path(temp_dir)
                
                # Configure download
                ydl_opts = self.ydl_opts.copy()
                if quality == 'audio':
                    ydl_opts.update({
                        'format': 'bestaudio/best',
                        'postprocessors': [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }],
                    })
                elif quality == 'fast':
                    ydl_opts['format'] = 'worst[ext=mp4]'
                
                ydl_opts['outtmpl'] = str(temp_path / 'video.%(ext)s')
                
                # Download
                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, download)
                
                # Find downloaded file
                files = list(temp_path.glob('*'))
                if not files:
                    raise Exception("No file downloaded")
                
                file_path = max(files, key=lambda f: f.stat().st_mtime)
                file_size = file_path.stat().st_size
                
                # Check size
                if file_size > MAX_FILE_SIZE:
                    await status_msg.edit_text(
                        f"❌ File too large!\n"
                        f"Size: {self._format_size(file_size)}\n"
                        f"Max: {self._format_size(MAX_FILE_SIZE)}"
                    )
                    return
                
                # Upload status
                await status_msg.edit_text(
                    f"📤 *Uploading...*\n"
                    f"📊 Size: {self._format_size(file_size)}\n"
                    f"⏳ Almost done!",
                    parse_mode='Markdown'
                )
                
                # Send file
                with open(file_path, 'rb') as file:
                    if quality == 'audio':
                        await update.message.reply_audio(
                            audio=InputFile(file),
                            caption=f"✅ Audio downloaded!\nSize: {self._format_size(file_size)}",
                            timeout=300
                        )
                    else:
                        await update.message.reply_video(
                            video=InputFile(file),
                            caption=f"✅ Video downloaded!\nSize: {self._format_size(file_size)}",
                            supports_streaming=True,
                            timeout=300
                        )
                
                # Cleanup
                try:
                    await status_msg.delete()
                except:
                    pass
                
                logger.info(f"Sent {self._format_size(file_size)} to user {user.id}")
                
        except asyncio.CancelledError:
            await status_msg.edit_text("❌ Download cancelled.")
            logger.info(f"Download cancelled for user {user.id}")
            
        except Exception as e:
            logger.error(f"Download error for user {user.id}: {e}")
            
            error_msg = "❌ Error downloading video."
            if "File too large" in str(e):
                error_msg = "❌ File too large (max 500MB)."
            elif "timeout" in str(e).lower():
                error_msg = "❌ Timeout. Try again with `-fast` flag."
            
            try:
                await status_msg.edit_text(error_msg)
            except:
                await update.message.reply_text(error_msg)
        finally:
            if user.id in self.active_downloads:
                del self.active_downloads[user.id]
    
    async def cleanup(self):
        """Clean up temporary files."""
        if self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
                logger.info("Cleaned temp directory")
            except Exception as e:
                logger.error(f"Cleanup error: {e}")

# ============ MAIN FUNCTION ============
async def main():
    """Main function - SIMPLIFIED for PTB v20+."""
    print("=" * 50)
    print("🚀 Telegram Video Bot - Render.com")
    print("=" * 50)
    print(f"✅ Bot Token: {'Set' if BOT_TOKEN else 'NOT SET'}")
    print("🔄 Starting bot...")
    print("=" * 50)
    
    # Create bot manager
    bot = VideoDownloaderBot()
    
    # Create application - SIMPLE version
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot.start_command))
    application.add_handler(CommandHandler("help", bot.help_command))
    application.add_handler(CommandHandler("stats", bot.stats_command))
    application.add_handler(CommandHandler("cancel", bot.cancel_command))
    
    # Handle text messages
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        bot.handle_message
    ))
    
    # Error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Bot error: {context.error}")
    
    application.add_error_handler(error_handler)
    
    # Start the bot - SIMPLE way
    print("🤖 Bot is starting...")
    print("📡 Ready to receive messages!")
    print("=" * 50)
    
    await application.run_polling(drop_pending_updates=True)

# ============ ENTRY POINT ============
if __name__ == '__main__':
    try:
        # Run the bot
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 Critical error: {e}")
        traceback.print_exc()
        sys.exit(1)
