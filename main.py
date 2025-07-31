import discord
from discord.ext import commands
from discord.ui import View, Button
from flask import Flask
from threading import Thread
import os
import time
import uuid

TOKEN = os.environ["DISCORD_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

STAFF_ROLE_ID = 1236054387751784531
OWNER_ROLE_ID = 1236055087214760077
TICKET_CATEGORY_ID = 1400236955278643341
ARCHIVE_CATEGORY_ID = 1400288162370293810
BUTTON_CHANNEL_ID = 1400288278992781332

user_ticket_map = {}            # user_id -> channel_id
ticket_owner_lookup = {}        # channel_id -> user_id
ticket_target_lookup = {}       # user_id -> reported name
ticket_cooldowns = {}           # user_id -> timestamp
message_history = {}            # user_id -> [timestamps]
COOLDOWN_SECONDS = 300          # 5 minutes
SPAM_LIMIT = 5                  # messages
SPAM_WINDOW = 10                # seconds

# Keep-alive web server for Replit
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive! Use an external uptime monitor like UptimeRobot to keep me awake."

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.start()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    channel = bot.get_channel(BUTTON_CHANNEL_ID)
    if channel:
        async for msg in channel.history(limit=50):
            if msg.author == bot.user:
                await msg.delete()
        view = TicketButtonView()
        await channel.send("🎫 Want to make a report? Click the button below to create one!:", view=view)

class TicketButtonView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(OpenTicketButton())

class OpenTicketButton(Button):
    def __init__(self):
        super().__init__(label="Make A Report!", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.user.send("👤 What is the name of the person(s) you want to report?")
            await interaction.response.send_message("✅ Check your DMs to start the report.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I can't DM you. Please check your privacy settings.", ephemeral=True)

@bot.event
async def on_message(message):
    await bot.process_commands(message)

    if message.author.bot:
        return

    if isinstance(message.channel, discord.DMChannel):
        now = time.time()
        cooldown = ticket_cooldowns.get(message.author.id, 0)
        if message.author.id not in user_ticket_map and now < cooldown:
            remaining = int(cooldown - now)
            try:
                await message.author.send(f"⏳ You need to wait {remaining} seconds before opening a new ticket.")
            except:
                pass
            return

        history = message_history.setdefault(message.author.id, [])
        history.append(now)
        history = [t for t in history if now - t <= SPAM_WINDOW]
        message_history[message.author.id] = history
        if len(history) > SPAM_LIMIT:
            try:
                await message.author.send("⚠️ You're sending messages too quickly. Please slow down.")
            except:
                pass

        guild = bot.guilds[0]
        user_id = message.author.id
        channel_id = user_ticket_map.get(user_id)
        channel = guild.get_channel(channel_id) if channel_id else None

        files = [await a.to_file() for a in message.attachments] if message.attachments else None

        if user_id not in ticket_target_lookup:
            ticket_target_lookup[user_id] = message.content.strip()
            await message.author.send("📝 Please describe the issue you want to report.")
            return

        if channel and channel.category and channel.category.id == TICKET_CATEGORY_ID:
            content = message.content or "(no text)"
            await channel.send(content, files=files or [])
            try:
                await message.author.send("📨 Message added to your ticket.")
            except:
                pass
            return

        category = discord.utils.get(guild.categories, id=TICKET_CATEGORY_ID)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.get_role(OWNER_ROLE_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.get_role(STAFF_ROLE_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        unique_id = str(uuid.uuid4())[:8]
        reported_name = ticket_target_lookup.get(user_id, "unknown")
        new_channel = await guild.create_text_channel(
            name=f"ticket-{reported_name}-{unique_id}", category=category, overwrites=overwrites
        )

        user_ticket_map[user_id] = new_channel.id
        ticket_owner_lookup[new_channel.id] = user_id
        ticket_cooldowns[user_id] = now + COOLDOWN_SECONDS

        content = message.content or "(no text)"
        first_msg = await new_channel.send(f"📩 New Report! (Target: **{reported_name}**):\n{content}", files=files or [])
        await first_msg.pin()

        try:
            await message.author.send("✅ Your anonymous report has been sent to staff! Someone will get back to you when available.")
        except:
            pass
        return

    elif message.guild and message.channel.category and message.channel.category.id == TICKET_CATEGORY_ID:
        user_id = ticket_owner_lookup.get(message.channel.id)
        if user_id:
            try:
                user = await bot.fetch_user(user_id)
                files = [await a.to_file() for a in message.attachments] if message.attachments else None
                content = f"💬 Staff reply:\n{message.content}" if message.content else None
                if content or files:
                    await user.send(content, files=files or [])
                    await message.channel.send("✅ Reply sent to reporter.")
            except:
                await message.channel.send("⚠️ Could not send message to user.")

@bot.command()
@commands.has_role(STAFF_ROLE_ID)
async def close(ctx):
    user_id = ticket_owner_lookup.get(ctx.channel.id)
    if user_id:
        try:
            user = await bot.fetch_user(user_id)
            await user.send("🔒 Your ticket has been closed. Thank you!")
        except:
            pass
        user_ticket_map.pop(user_id, None)

    archive_cat = discord.utils.get(ctx.guild.categories, id=ARCHIVE_CATEGORY_ID)
    await ctx.channel.edit(category=archive_cat)
    await ctx.send("🗂️ Ticket has been archived.")

@bot.command()
@commands.has_role(OWNER_ROLE_ID)
async def identify(ctx):
    user_id = ticket_owner_lookup.get(ctx.channel.id)
    if user_id:
        user = await bot.fetch_user(user_id)
        await ctx.send(f"🕵️ Reporter: {user.name}#{user.discriminator} ({user.id})")
    else:
        await ctx.send("❓ Could not identify the reporter.")

@bot.command()
@commands.has_role(OWNER_ROLE_ID)
async def forget(ctx):
    if ctx.channel.id in ticket_owner_lookup:
        ticket_owner_lookup.pop(ctx.channel.id)
        await ctx.send("🧹 Ticket owner info has been forgotten.")
    else:
        await ctx.send("ℹ️ No owner info stored for this ticket.")

keep_alive()
bot.run(TOKEN)