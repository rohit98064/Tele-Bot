import os
from pytube import YouTube
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import logging
import asyncio

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable (for security)
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# Store user sessions (simple in-memory storage)
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "👋 **YouTube Video Downloader Bot**\n\n"
        "📥 Just send me any YouTube video URL and I'll download it for you!\n\n"
        "⚡ **Features:**\n"
        "• Auto 1080p MP4 download\n"
        "• Multiple resolution options\n"
        "• Fast downloading\n\n"
        "📋 **Commands:**\n"
        "/start - Show this message\n"
        "/help - Show help\n"
        "/about - About this bot"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send help message."""
    await update.message.reply_text(
        "❓ **How to use:**\n\n"
        "1. Send me a YouTube video URL\n"
        "2. I'll try to download 1080p MP4 version\n"
        "3. If 1080p not available, I'll show you all available resolutions\n"
        "4. Choose a resolution by number\n\n"
        "📌 **Supported URLs:**\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• Shorts and playlists (single videos)\n\n"
        "⚠️ **Limitations:**\n"
        "• Max 2GB file size (Telegram limit)\n"
        "• Videos < 50 mins work best"
    )

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send about message."""
    await update.message.reply_text(
        "🤖 **YT Video Downloader Bot**\n\n"
        "📅 Version: 1.0\n"
        "🛠 Created by: Rohit\n"
        "📚 Powered by: pytube & python-telegram-bot\n\n"
        "📍 Hosted on: Render Cloud\n"
        "⚡ Status: Online 24/7"
    )

async def handle_video_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube URL."""
    user_id = update.effective_user.id
    url = update.message.text.strip()
    
    # Validate URL
    if not ("youtube.com" in url or "youtu.be" in url):
        await update.message.reply_text("❌ Please send a valid YouTube URL")
        return
    
    try:
        # Delete any previous session
        if user_id in user_sessions:
            del user_sessions[user_id]
        
        # Send processing message
        processing_msg = await update.message.reply_text("⏳ Processing URL...")
        
        # Get video info
        yt = YouTube(url)
        
        # Get best thumbnail
        thumbnail_url = yt.thumbnail_url
        
        # Send video info
        info_msg = await update.message.reply_text(
            f"🎬 **{yt.title}**\n"
            f"👤 Channel: {yt.author}\n"
            f"⏱ Duration: {yt.length//60}:{yt.length%60:02d}\n"
            f"👁 Views: {yt.views:,}\n\n"
            f"🔍 Searching for 1080p quality..."
        )
        
        # Look for 1080p
        hd_streams = yt.streams.filter(
            file_extension='mp4', 
            res="1080p",
            progressive=True
        ).first()
        
        if hd_streams:
            await info_msg.edit_text(
                f"✅ **1080p Quality Available!**\n"
                f"📊 Resolution: 1080p\n"
                f"💾 Size: {hd_streams.filesize_mb:.1f} MB\n\n"
                f"⏬ Downloading..."
            )
            
            # Download with progress
            await download_and_send_video(update, hd_streams, yt.title, "1080p")
            
        else:
            # Show available resolutions
            available_streams = yt.streams.filter(
                file_extension='mp4', 
                progressive=True
            ).order_by('resolution').desc()
            
            if not available_streams:
                await info_msg.edit_text("❌ No MP4 streams available for this video.")
                return
            
            resolutions_text = "📋 **Available Resolutions:**\n\n"
            streams_list = []
            
            for i, stream in enumerate(available_streams, 1):
                if stream.resolution:
                    size_mb = stream.filesize_mb if stream.filesize_mb else "Unknown"
                    resolutions_text += f"{i}. {stream.resolution} ({stream.fps}fps) - {size_mb:.1f}MB\n"
                    streams_list.append(stream)
            
            resolutions_text += "\nReply with number to download (e.g., '1')"
            
            await info_msg.edit_text(resolutions_text)
            
            # Store session
            user_sessions[user_id] = {
                'streams': streams_list,
                'video_title': yt.title,
                'message_id': info_msg.message_id
            }
        
        # Clean up processing message
        await processing_msg.delete()
        
    except Exception as e:
        logger.error(f"Error processing URL: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def handle_resolution_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user's resolution choice."""
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text("❌ No active session. Please send a YouTube URL first.")
        return
    
    try:
        choice = int(update.message.text.strip())
        session = user_sessions[user_id]
        streams = session['streams']
        video_title = session['video_title']
        
        if 1 <= choice <= len(streams):
            selected_stream = streams[choice - 1]
            
            # Send downloading message
            download_msg = await update.message.reply_text(
                f"⏬ Downloading {selected_stream.resolution}...\n"
                f"📊 Size: {selected_stream.filesize_mb:.1f} MB\n"
                f"Please wait..."
            )
            
            # Download and send
            await download_and_send_video(
                update, 
                selected_stream, 
                video_title, 
                selected_stream.resolution
            )
            
            # Clean up
            await download_msg.delete()
            del user_sessions[user_id]
            
        else:
            await update.message.reply_text(f"❌ Please choose a number between 1 and {len(streams)}")
    
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number")
    except Exception as e:
        logger.error(f"Error in resolution choice: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def download_and_send_video(update: Update, stream, title, resolution):
    """Download video and send to user."""
    try:
        # Create downloads directory
        os.makedirs("downloads", exist_ok=True)
        
        # Generate safe filename
        safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename = f"downloads/{safe_title[:50]}_{resolution}.mp4"
        
        # Download the video
        download_path = stream.download(output_path="downloads", filename=f"{safe_title[:50]}.mp4")
        
        # Send to user
        with open(download_path, 'rb') as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=f"🎬 {title}\n📊 Resolution: {resolution}",
                supports_streaming=True,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=60
            )
        
        # Clean up file
        os.remove(download_path)
        
    except Exception as e:
        logger.error(f"Error downloading/sending video: {e}")
        await update.message.reply_text(f"❌ Download failed: {str(e)}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")
    
    if update and hasattr(update, 'effective_user'):
        try:
            await update.effective_user.send_message(
                "❌ Sorry, something went wrong. Please try again later."
            )
        except:
            pass

def main():
    """Start the bot."""
    # Check for token
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("❌ ERROR: Please set BOT_TOKEN environment variable")
        print("On Render: Add in Environment Variables")
        print("Locally: export BOT_TOKEN='your_token_here'")
        return
    
    # Create Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    
    # Message handlers
    application.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'(youtube\.com|youtu\.be)'), 
        handle_video_url
    ))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_resolution_choice
    ))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    print("🤖 Bot starting...")
    print(f"✅ Token: {'Set' if BOT_TOKEN != 'YOUR_BOT_TOKEN_HERE' else 'Not Set'}")
    print("📡 Bot is now running on Render!")
    
    # Run bot
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )

if __name__ == '__main__':
    main()