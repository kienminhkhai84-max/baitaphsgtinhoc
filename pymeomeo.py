# bot.py
import discord
from discord.ext import commands
import pytesseract
from PIL import Image
import aiohttp
import io
import re
import os
import asyncio
import datetime

# ==== CONFIG ====
TOKEN = "PUT-YOUR-TOKEN-HERE"   # 🔴 Thay token của bạn vào đây (hoặc dùng .env)
PREFIX = "!"
INTENTS = discord.Intents.default()
INTENTS.messages = True
INTENTS.message_content = True
INTENTS.guilds = True
INTENTS.members = True

bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS)

# ====== Scam keywords ======
scam_keywords = [
    "free nitro", "discord-nitro", "steam-free", "airdrop", "gift-link",
    "http://", "https://", ".ru", "grabnitro", "nitrogift",
    "withdraw", "withdrawal", "withdrawn", "withdrawal success",
    "$2500", "2500 usdt", "2500.00", "receive usdt", "receive $",
    "claim reward", "claim your reward", "promo code", "special promo",
    "bonus", "casino", "crypto casino", "cerplays", "cerplays.com",
    "register to receive", "you can withdraw", "giving away", "giveaway",
    "you've received", "withdrawal successfull", "withdrawal successful"
]

OCR_KEYWORDS = [
    "gift", "withdrawal", "withdraw", "promo", "register", "bonus", "$2500",
    "casino", "claim", "free", "receive usdt", "withdrawal success"
]

# Timeout duration: 4 weeks (28 days)
TIMEOUT_SECONDS = 28 * 24 * 60 * 60

# ====== Helper: check scam keywords ======
def contains_scam_keyword(text: str, keywords: list) -> bool:
    if not text:
        return False
    txt = text.lower()
    for kw in keywords:
        if kw in txt:
            return True
    # pattern tiền tệ: $2500, 2,500$, 500.00$
    if re.search(r'\$\s?\d{3,}|\d{1,3}(?:[.,]\d{3})*(?:\.\d+)?\s?\$', txt):
        return True
    return False

# ====== OCR ======
async def ocr_image(url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGB")
                text = pytesseract.image_to_string(img)
                return text.lower()
    except Exception as e:
        print(f"OCR error: {e}")
        return ""

# ====== Timeout helpers ======

async def ensure_autotimeout_role(guild: discord.Guild) -> discord.Role:
    """Tạo role AutoTimeout nếu chưa có, và set permission deny ở toàn bộ channel."""
    role = discord.utils.get(guild.roles, name="AutoTimeout")
    if role:
        return role
    try:
        role = await guild.create_role(name="AutoTimeout", permissions=discord.Permissions.none(), reason="Role for timeout fallback")
    except Exception as e:
        print(f"Failed creating AutoTimeout role: {e}")
        return None

    # Apply channel overrides
    for ch in guild.channels:
        try:
            await ch.set_permissions(role, send_messages=False, add_reactions=False, speak=False, view_channel=True)
        except Exception:
            # ignore channels where bot lacks permission
            pass
    return role

async def remove_role_after(member: discord.Member, role: discord.Role, delay: int):
    """Gỡ role sau delay giây. LƯU Ý: không bền qua restart."""
    await asyncio.sleep(delay)
    try:
        if role in member.roles:
            await member.remove_roles(role, reason="AutoTimeout expired")
    except Exception as e:
        print(f"Error removing role after timeout: {e}")

async def apply_timeout(member: discord.Member, reason: str, duration_seconds: int):
    """
    Cố dùng discođrd timeout (communication_disabled_until) nếu bot có quyền.
    Nếu fail, fallback dùng role AutoTimeout.
    """
    guild = member.guild
    # first try native moderation timeout (Discord built-in)
    try:
        until = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
        # Member.edit with communication_disabled_until is supported in discord.py v2.x
        await member.edit(communication_disabled_until=until, reason=reason)
        return "native_timeout"
    except Exception as e:
        # fallback to role method
        print(f"Native timeout failed or not available: {e}")

    role = await ensure_autotimeout_role(guild)
    if not role:
        return "failed"

    try:
        await member.add_roles(role, reason=reason)
        # schedule removal (non-persistent)
        asyncio.create_task(remove_role_after(member, role, duration_seconds))
        return "role_timeout"
    except Exception as e:
        print(f"Failed to add role AutoTimeout: {e}")
        return "failed"

# ====== Punish: immediate timeout 4 weeks ======
async def punish_timeout(message: discord.Message, reason: str):
    author = message.author
    # delete message first
    try:
        await message.delete()
    except Exception:
        pass

    mod_log = None
    try:
        # try find a mod log channel named 'mod-logs' (optional)
        mod_log = discord.utils.get(message.guild.text_channels, name="mod-logs")
    except:
        mod_log = None

    applied = await apply_timeout(author, reason=f"Auto timeout for scam detection: {reason}", duration_seconds=TIMEOUT_SECONDS)

    # notify mod / channel
    note = f"⛔ {author.mention} has been timed out for 4 weeks. Method: {applied}. Reason: {reason}"
    try:
        if mod_log:
            await mod_log.send(note)
        else:
            # fallback: send in same channel but delete after short time
            await message.channel.send(note, delete_after=20)
    except:
        pass

# ====== Events ======
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập thành công với tên {bot.user}!")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Immediate check: text
    if contains_scam_keyword(message.content, scam_keywords):
        await punish_timeout(message, "keyword")
        return  # stop further processing

    # Check attachments via OCR
    for att in message.attachments:
        if any(att.filename.lower().endswith(ext) for ext in ["png", "jpg", "jpeg", "webp"]):
            text = await ocr_image(att.url)
            if contains_scam_keyword(text, OCR_KEYWORDS):
                await punish_timeout(message, "ocr_keyword")
                return

    await bot.process_commands(message)

# ====== Admin Commands ======
@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int = 10):
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Đã xoá {amount} tin nhắn!", delete_after=5)

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="Không có lý do"):
    await member.kick(reason=reason)
    await ctx.send(f"👢 {member.mention} đã bị kick. Lý do: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="Không có lý do"):
    await member.ban(reason=reason)
    await ctx.send(f"⛔ {member.mention} đã bị ban. Lý do: {reason}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx, member: discord.Member):
    """Gỡ timeout thủ công (native timeout) hoặc remove role fallback."""
    # try native remove
    try:
        await member.edit(communication_disabled_until=None, reason=f"Timeout removed by {ctx.author}")
    except Exception:
        pass

    # remove role fallback
    role = discord.utils.get(ctx.guild.roles, name="AutoTimeout")
    if role and role in member.roles:
        try:
            await member.remove_roles(role, reason=f"Timeout removed by {ctx.author}")
        except Exception:
            pass

    await ctx.send(f"✅ Đã gỡ timeout cho {member.mention}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def lockdown(ctx):
    """Lockdown: prevent @everyone gửi tin nhắn (khi bị raid)."""
    everyone = ctx.guild.default_role
    for ch in ctx.guild.channels:
        try:
            await ch.set_permissions(everyone, send_messages=False)
        except:
            pass
    await ctx.send("🔒 Server đã được lockdown (không ai gửi tin nhắn được).")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    everyone = ctx.guild.default_role
    for ch in ctx.guild.channels:
        try:
            await ch.set_permissions(everyone, send_messages=None)
        except:
            pass
    await ctx.send("🔓 Server đã mở lại.")

# Run
bot.run(TOKEN)