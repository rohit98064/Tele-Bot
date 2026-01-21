#!/usr/bin/env python3
"""
Telegram Video Downloader Bot - Render.com Deployment
Properly indented and fixed version
"""

import os
import sys
import logging
import tempfile
import asyncio
import math
import time
import signal
import psutil
import traceback
import re
from typing import Dict, Optional, List, Tuple
from pathlib import Path
import shutil
from dataclasses import dataclass, field
from urllib.parse import urlparse

import yt_dlp
from telegram import Update, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ============ CONFIGURATION ============
PORT = int(os.environ.get('PORT', 10000))
HOST = '0.0.0.0'

# Get bot token from environment
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable is not set!")
    print("Please set it in Render's Environment settings.")
    sys.exit(1)

# Settings
MAX_FILE_SIZE = 1.8 * 1024 * 1024 * 1024  # 1.8GB
MAX_VIDEO_DURATION = 3600  # 1 hour
MAX_REQUESTS_PER_USER = 10
MAX_CONCURRENT_DOWNLOADS = 3

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

# ============ DATA CLASSES ============
@dataclass
class DownloadRequest:
    user_id: int
    url: str
    platform: str
    timestamp: float
    status: str = "pending"
    file_size: int = 0
    duration: int = 0
    quality: str = "best"
    message_id: Optional[int] = None
    
    @property
    def age(self) -> float:
        return time.time() - self.timestamp

@dataclass  
class UserStats:
    user_id: int
    username: Optional[str] = None
    downloads: int = 0
    last_active: float = field(default_factory=time.time)
    total_size: int = 0
    
    def update_activity(self):
        self.last_active = time.time()

# ============ MAIN BOT CLASS ============
class VideoDownloaderBot:
    def __init__(self):
        self.requests = {}
        self.user_stats = {}
        self.active_downloads = {}
        self.download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        
        # Platform detection
        self.supported_platforms = {
            'youtube': [r'youtube\.com', r'youtu\.be', r'y2u\.be'],
            'tiktok': [r'tiktok\.com', r'vm\.tiktok\.com', r'vt\.tiktok\.com'],
            'instagram': [r'instagram\.com', r'instagr\.am'],
            'twitter': [r'twitter\.com', r'x\.com', r't\.co'],
            'facebook': [r'facebook\.com', r'fb\.watch', r'fb\.com'],
            'reddit': [r'reddit\.com', r'redd\.it'],
            'twitch': [r'twitch\.tv', r'clips\.twitch\.tv'],
            'dailymotion': [r'dailymotion\.com', r'dai\.ly'],
        }
        
        # Compile patterns
        self.platform_patterns = {}
        for platform, patterns in self.supported_platforms.items():
            compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
            self.platform_patterns[platform] = compiled
        
        # yt-dlp options
        self.ydl_opts = {
            'format': 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': '%(title).100s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'retries': 2,
            'fragment_retries': 2,
            'socket_timeout': 10,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            },
            'concurrent_fragment_downloads': 1,
        }
        
        # Statistics
        self.stats = {
            'total_downloads': 0,
            'total_size_downloaded': 0,
            'start_time': time.time(),
            'errors': 0,
            'unique_users': set()
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
        size_name = ("B", "KB", "MB", "GB", "TB")
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
            secs = seconds % 60
            return f"{hours}h {minutes}m {secs}s"

    def _detect_platform(self, url: str) -> Optional[str]:
        """Detect which platform the URL belongs to."""
        try:
            domain = urlparse(url).netloc.lower()
            for platform, patterns in self.platform_patterns.items():
                for pattern in patterns:
                    if pattern.search(domain):
                        return platform
            return None
        except Exception as e:
            logger.debug(f"Error detecting platform: {e}")
            return None

    @staticmethod
    def _extract_url(text: str) -> Optional[str]:
        """Extract URL from text."""
        url_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\.\-?=&%#+]*'
        matches = re.findall(url_pattern, text)
        return matches[0] if matches else None

    # ============ COMMAND HANDLERS ============
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        
        # Update stats
        self.stats['unique_users'].add(user.id)
        
        if user.id not in self.user_stats:
            self.user_stats[user.id] = UserStats(
                user_id=user.id,
                username=user.username
            )
        
        welcome_text = f"""
👋 *Welcome {user.first_name}!*

🎬 *Video Downloader Bot*
⚡ *Deployed on Render.com*

📥 *Supported Platforms:*
• YouTube, TikTok, Instagram
• Twitter/X, Facebook, Reddit
• Twitch, Dailymotion, and more!

🚀 *How to use:* Just send any video link!

📊 *Your Statistics:*
• Downloads: {self.user_stats[user.id].downloads}
• Total Size: {self._format_size(self.user_stats[user.id].total_size)}

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

🎯 *Quality Flags (add after URL):*
-best - Best quality
-720 - 720p HD
-1080 - 1080p Full HD
-audio - Audio only (MP3)
-fast - Faster download

⚠️ *Limits:*
• Max file: 2GB
• Max duration: 1 hour
• Rate limit: 10 downloads/hour

💡 *Tip:* For long videos, use `-fast` flag!
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command."""
        memory = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)
        uptime = time.time() - self.stats['start_time']
        
        stats_text = f"""
📊 *Bot Statistics*

👥 *Users:*
• Unique: {len(self.stats['unique_users'])}
• Active: {len(self.active_downloads)}

📥 *Downloads:*
• Total: {self.stats['total_downloads']}
• Size: {self._format_size(self.stats['total_size_downloaded'])}
• Errors: {self.stats['errors']}

⚙️ *Server Status:*
• CPU: {cpu:.1f}%
• Memory: {memory.percent:.1f}%
• Uptime: {self._format_duration(int(uptime))}

🏓 *Health:* ✅ Online on Render
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
        
        # Update user activity
        if user.id not in self.user_stats:
            self.user_stats[user.id] = UserStats(
                user_id=user.id,
                username=user.username
            )
        self.user_stats[user.id].update_activity()
        
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
                "Supported: YouTube, TikTok, Instagram, Twitter, Facebook, Reddit, etc."
            )
            return
        
        # Check quality flags
        quality = "best"
        if "-fast" in message_text.lower():
            quality = "fast"
        elif "-720" in message_text.lower():
            quality = "720"
        elif "-1080" in message_text.lower():
            quality = "1080"
        elif "-audio" in message_text.lower():
            quality = "audio"
        
        # Check active downloads
        if user.id in self.active_downloads:
            task = self.active_downloads[user.id]
            if not task.done():
                await update.message.reply_text(
                    "⚠️ You have a download in progress!\n"
                    "Use /cancel to cancel it first."
                )
                return
        
        # Process download
        async with self.download_semaphore:
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
                if fut.exception():
                    logger.error(f"Download error: {fut.exception()}")
            
            task.add_done_callback(cleanup)
            
        except Exception as e:
            logger.error(f"Process error: {e}")
            self.stats['errors'] += 1
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
                f"🎯 Quality: {quality}\n"
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
                    ydl_opts['format'] = 'best[height<=480]/worst'
                elif quality.isdigit():
                    ydl_opts['format'] = f'best[height<={quality}]/worst'
                
                ydl_opts['outtmpl'] = str(temp_path / '%(title)s.%(ext)s')
                
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
                
                # Update stats
                self.stats['total_downloads'] += 1
                self.stats['total_size_downloaded'] += file_size
                
                if user.id in self.user_stats:
                    self.user_stats[user.id].downloads += 1
                    self.user_stats[user.id].total_size += file_size
                
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
                            audio=InputFile(file, filename=file_path.name),
                            caption=f"✅ Audio downloaded!\nSize: {self._format_size(file_size)}",
                            timeout=300
                        )
                    else:
                        await update.message.reply_video(
                            video=InputFile(file, filename=file_path.name),
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
            self.stats['errors'] += 1
            
            error_msg = "❌ Error downloading video."
            if "File too large" in str(e):
                error_msg = "❌ File too large (max 2GB)."
            elif "timeout" in str(e).lower():
                error_msg = "❌ Timeout. Try again with lower quality."
            
            try:
                await status_msg.edit_text(error_msg)
            except:
                await update.message.reply_text(error_msg)
        finally:
            if user.id in self.active_downloads:
                del self.active_downloads[user.id]

    async def cleanup_temp_files(self):
        """Clean up temporary files."""
        while True:
            try:
                now = time.time()
                for item in self.temp_dir.glob('*'):
                    try:
                        if item.is_file() and (now - item.stat().st_mtime > 1800):
                            item.unlink(missing_ok=True)
                        elif item.is_dir() and (now - item.stat().st_mtime > 1800):
                            shutil.rmtree(item, ignore_errors=True)
                    except:
                        pass
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
            
            await asyncio.sleep(900)  # Every 15 minutes

    async def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        
        # Cancel downloads
        for task in self.active_downloads.values():
            if not task.done():
                task.cancel()
        
        # Wait briefly
        if self.active_downloads:
            await asyncio.sleep(2)
        
        # Clean temp
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        
        logger.info("Shutdown complete")

# ============ MAIN FUNCTION ============
async def main():
    """Main entry point for Render."""
    print("=" * 60)
    print("🎬 TELEGRAM VIDEO BOT - RENDER.COM")
    print("=" * 60)
    print(f"🤖 Bot Token: {'✅ Set' if BOT_TOKEN else '❌ Not Set'}")
    print(f"🌐 Host: {HOST}:{PORT}")
    print(f"🚀 Starting bot...")
    print("=" * 60)
    
    # Create bot manager
    bot_manager = VideoDownloaderBot()
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_manager.start_command))
    application.add_handler(CommandHandler("help", bot_manager.help_command))
    application.add_handler(CommandHandler("stats", bot_manager.stats_command))
    application.add_handler(CommandHandler("cancel", bot_manager.cancel_command))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        bot_manager.handle_message
    ))
    
    # Add error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}", exc_info=context.error)
    
    application.add_error_handler(error_handler)
    
    # Store bot manager
    application.bot_data['manager'] = bot_manager
    
    # Start cleanup task
    cleanup_task = asyncio.create_task(bot_manager.cleanup_temp_files())
    
    try:
        # Start the bot - FIXED: PROPER INDENTATION
        print("🚀 Starting bot polling...")
        await application.initialize()
        await application.start()
        
        print("✅ Bot started successfully on Render!")
        print("📡 Listening for messages...")
        
        # FIXED: This is the critical line - PROPERLY INDENTED
        await application.run_polling()
        
    except asyncio.CancelledError:
        logger.info("Shutdown signal received")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        traceback.print_exc()
        raise
    finally:
        # Clean shutdown - PROPERLY INDENTED
        print("\n🧹 Cleaning up...")
        try:
            cleanup_task.cancel()
            await bot_manager.shutdown()
            await application.stop()
            await application.shutdown()
            print("✅ Cleanup complete")
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")

# ============ ENTRY POINT ============
if __name__ == '__main__':
    # Check Python version
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ required")
        sys.exit(1)
    
    # Run the bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 Error: {e}")
        sys.exit(1)    user_id: int
    url: str
    platform: str
    timestamp: float
    status: str = "pending"
    file_size: int = 0
    duration: int = 0
    quality: str = "best"
    message_id: Optional[int] = None
    
    @property
    def age(self) -> float:
        return time.time() - self.timestamp

@dataclass  
class UserStats:
    user_id: int
    username: Optional[str] = None
    downloads: int = 0
    last_active: float = field(default_factory=time.time)
    total_size: int = 0
    
    def update_activity(self):
        self.last_active = time.time()

# ============ HEALTH CHECK SERVER ============
class HealthCheckServer:
    def __init__(self, port=8080):
        self.port = port
        self.server = None
        self.thread = None
        
    def start(self):
        """Start health check server in background thread."""
        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/health':
                    self.send_response(200)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'OK')
                else:
                    self.send_response(404)
                    self.end_headers()
            
            def log_message(self, format, *args):
                # Suppress logs
                pass
        
        def run_server():
            with socketserver.TCPServer((HOST, self.port), HealthHandler) as httpd:
                self.server = httpd
                logger.info(f"Health check server running on port {self.port}")
                httpd.serve_forever()
        
        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop health check server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()

# ============ MAIN BOT CLASS ============
class VideoDownloaderBot:
    def __init__(self):
        self.requests = {}
        self.user_stats = {}
        self.active_downloads = {}
        self.download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
        
        # Platform detection
        self.supported_platforms = {
            'youtube': [r'youtube\.com', r'youtu\.be', r'y2u\.be'],
            'tiktok': [r'tiktok\.com', r'vm\.tiktok\.com', r'vt\.tiktok\.com'],
            'instagram': [r'instagram\.com', r'instagr\.am'],
            'twitter': [r'twitter\.com', r'x\.com', r't\.co'],
            'facebook': [r'facebook\.com', r'fb\.watch', r'fb\.com'],
            'reddit': [r'reddit\.com', r'redd\.it'],
            'twitch': [r'twitch\.tv', r'clips\.twitch\.tv'],
            'dailymotion': [r'dailymotion\.com', r'dai\.ly'],
        }
        
        # Compile patterns
        self.platform_patterns = {}
        for platform, patterns in self.supported_platforms.items():
            compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
            self.platform_patterns[platform] = compiled
        
        # yt-dlp options
        self.ydl_opts = {
            'format': 'best[height<=720][ext=mp4]/best[ext=mp4]/best',
            'merge_output_format': 'mp4',
            'outtmpl': '%(title).100s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'retries': 2,
            'fragment_retries': 2,
            'socket_timeout': 10,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            },
            'concurrent_fragment_downloads': 1,
        }
        
        # Statistics
        self.stats = {
            'total_downloads': 0,
            'total_size_downloaded': 0,
            'start_time': time.time(),
            'errors': 0,
            'unique_users': set()
        }
        
        # Temp directory
        self.temp_dir = Path(tempfile.mkdtemp(prefix="video_bot_"))
        
        # Health check
        self.health_server = HealthCheckServer(port=8080)
        
        logger.info(f"Bot initialized. Temp dir: {self.temp_dir}")

    # ============ HELPER METHODS ============
    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes == 0:
            return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB")
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
            secs = seconds % 60
            return f"{hours}h {minutes}m {secs}s"

    def _detect_platform(self, url: str) -> Optional[str]:
        """Detect which platform the URL belongs to."""
        try:
            domain = urlparse(url).netloc.lower()
            for platform, patterns in self.platform_patterns.items():
                for pattern in patterns:
                    if pattern.search(domain):
                        return platform
            return None
        except Exception as e:
            logger.debug(f"Error detecting platform: {e}")
            return None

    @staticmethod
    def _extract_url(text: str) -> Optional[str]:
        """Extract URL from text."""
        url_pattern = r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[/\w\.\-?=&%#+]*'
        matches = re.findall(url_pattern, text)
        return matches[0] if matches else None

    # ============ COMMAND HANDLERS ============
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        
        # Update stats
        self.stats['unique_users'].add(user.id)
        
        if user.id not in self.user_stats:
            self.user_stats[user.id] = UserStats(
                user_id=user.id,
                username=user.username
            )
        
        welcome_text = f"""
👋 *Welcome {user.first_name}!*

🎬 *Video Downloader Bot*
⚡ *Deployed on Render.com*

📥 *Supported Platforms:*
• YouTube, TikTok, Instagram
• Twitter/X, Facebook, Reddit
• Twitch, Dailymotion, and more!

🚀 *How to use:* Just send any video link!

📊 *Your Statistics:*
• Downloads: {self.user_stats[user.id].downloads}
• Total Size: {self._format_size(self.user_stats[user.id].total_size)}

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

🎯 *Quality Flags (add after URL):*
-best - Best quality
-720 - 720p HD
-1080 - 1080p Full HD
-audio - Audio only (MP3)
-fast - Faster download

⚠️ *Limits:*
• Max file: 2GB
• Max duration: 1 hour
• Rate limit: 10 downloads/hour

💡 *Tip:* For long videos, use `-fast` flag!
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command."""
        memory = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)
        uptime = time.time() - self.stats['start_time']
        
        stats_text = f"""
📊 *Bot Statistics*

👥 *Users:*
• Unique: {len(self.stats['unique_users'])}
• Active: {len(self.active_downloads)}

📥 *Downloads:*
• Total: {self.stats['total_downloads']}
• Size: {self._format_size(self.stats['total_size_downloaded'])}
• Errors: {self.stats['errors']}

⚙️ *Server Status:*
• CPU: {cpu:.1f}%
• Memory: {memory.percent:.1f}%
• Uptime: {self._format_duration(int(uptime))}

🏓 *Health:* ✅ Online on Render
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
        
        # Update user activity
        if user.id not in self.user_stats:
            self.user_stats[user.id] = UserStats(
                user_id=user.id,
                username=user.username
            )
        self.user_stats[user.id].update_activity()
        
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
                "Supported: YouTube, TikTok, Instagram, Twitter, Facebook, Reddit, etc."
            )
            return
        
        # Check quality flags
        quality = "best"
        if "-fast" in message_text.lower():
            quality = "fast"
        elif "-720" in message_text.lower():
            quality = "720"
        elif "-1080" in message_text.lower():
            quality = "1080"
        elif "-audio" in message_text.lower():
            quality = "audio"
        
        # Check active downloads
        if user.id in self.active_downloads:
            task = self.active_downloads[user.id]
            if not task.done():
                await update.message.reply_text(
                    "⚠️ You have a download in progress!\n"
                    "Use /cancel to cancel it first."
                )
                return
        
        # Process download
        async with self.download_semaphore:
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
                if fut.exception():
                    logger.error(f"Download error: {fut.exception()}")
            
            task.add_done_callback(cleanup)
            
        except Exception as e:
            logger.error(f"Process error: {e}")
            self.stats['errors'] += 1
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
                f"🎯 Quality: {quality}\n"
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
                    ydl_opts['format'] = 'best[height<=480]/worst'
                elif quality.isdigit():
                    ydl_opts['format'] = f'best[height<={quality}]/worst'
                
                ydl_opts['outtmpl'] = str(temp_path / '%(title)s.%(ext)s')
                
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
                
                # Update stats
                self.stats['total_downloads'] += 1
                self.stats['total_size_downloaded'] += file_size
                
                if user.id in self.user_stats:
                    self.user_stats[user.id].downloads += 1
                    self.user_stats[user.id].total_size += file_size
                
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
                            audio=InputFile(file, filename=file_path.name),
                            caption=f"✅ Audio downloaded!\nSize: {self._format_size(file_size)}",
                            timeout=300
                        )
                    else:
                        await update.message.reply_video(
                            video=InputFile(file, filename=file_path.name),
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
            self.stats['errors'] += 1
            
            error_msg = "❌ Error downloading video."
            if "File too large" in str(e):
                error_msg = "❌ File too large (max 2GB)."
            elif "timeout" in str(e).lower():
                error_msg = "❌ Timeout. Try again with lower quality."
            
            try:
                await status_msg.edit_text(error_msg)
            except:
                await update.message.reply_text(error_msg)
        finally:
            if user.id in self.active_downloads:
                del self.active_downloads[user.id]

    async def cleanup_temp_files(self):
        """Clean up temporary files."""
        while True:
            try:
                now = time.time()
                for item in self.temp_dir.glob('*'):
                    try:
                        if item.is_file() and (now - item.stat().st_mtime > 1800):
                            item.unlink(missing_ok=True)
                        elif item.is_dir() and (now - item.stat().st_mtime > 1800):
                            shutil.rmtree(item, ignore_errors=True)
                    except:
                        pass
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
            
            await asyncio.sleep(900)  # Every 15 minutes

    async def shutdown(self):
        """Clean shutdown."""
        logger.info("Shutting down...")
        
        # Cancel downloads
        for task in self.active_downloads.values():
            if not task.done():
                task.cancel()
        
        # Wait briefly
        if self.active_downloads:
            await asyncio.sleep(2)
        
        # Clean temp
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        
        # Stop health server
        self.health_server.stop()
        
        logger.info("Shutdown complete")

# ============ MAIN APPLICATION ============
async def main():
    """Main entry point for Render."""
    print("=" * 60)
    print("🎬 TELEGRAM VIDEO BOT - RENDER.COM")
    print("=" * 60)
    print(f"🤖 Bot Token: {'✅ Set' if BOT_TOKEN else '❌ Not Set'}")
    print(f"🌐 Host: {HOST}:{PORT}")
    print(f"🚀 Starting bot...")
    print("=" * 60)
    
    # Create bot manager
    bot_manager = VideoDownloaderBot()
    
    # Create application - FIXED FOR PTB v20+
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot_manager.start_command))
    application.add_handler(CommandHandler("help", bot_manager.help_command))
    application.add_handler(CommandHandler("stats", bot_manager.stats_command))
    application.add_handler(CommandHandler("cancel", bot_manager.cancel_command))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        bot_manager.handle_message
    ))
    
    # Add error handler
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Error: {context.error}", exc_info=context.error)
    
    application.add_error_handler(error_handler)
    
    # Store bot manager
    application.bot_data['manager'] = bot_manager
    
    # Start health check server
    bot_manager.health_server.start()
    
    # Start cleanup task
    cleanup_task = asyncio.create_task(bot_manager.cleanup_temp_files())
    
    try:
        # Start the bot - FIXED: No updater in v20+
        await application.initialize()
        await application.start()
        
        logger.info("✅ Bot started successfully on Render!")
        print("✅ Bot is running!")
        print("📡 Listening for messages...")
        print("🏥 Health check: http://localhost:8080/health")
        
       # Just start polling
       await application.run_polling() 
        
    except asyncio.CancelledError:
        logger.info("Shutdown signal received")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        traceback.print_exc()
        raise
    finally:
        # Clean shutdown
        logger.info("Cleaning up...")
        try:
            cleanup_task.cancel()
            await bot_manager.shutdown()
            await application.stop()
            await application.shutdown()
            logger.info("Cleanup complete")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

if __name__ == '__main__':
    # Check Python version
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ required")
        sys.exit(1)
    
    # Run the bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    except Exception as e:
        print(f"\n💥 Error: {e}")
        sys.exit(1)
