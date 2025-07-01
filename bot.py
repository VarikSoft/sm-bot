import os
import re
import string
from itertools import product
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()
TOKEN = os.getenv("TOKEN")

# Set up intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Mapping short permission keys to Discord permission attributes
PERMISSION_MAP = {
    "view": "view_channel",
    "send": "send_messages",
    "connect": "connect",
    "speak": "speak",
    "manage": "manage_channels",
    "read": "read_message_history",
    "attach": "attach_files"
}

def interpret_template_string(format_str: str) -> list:
    """Parse a template string and expand ranges, grids, lists, and zero-padding."""
    # Pure numeric range without prefix: [start...end(:step)]
    m0 = re.match(r"\[\s*(\d+)\.\.\.(\d+)(?::(\d+))?\s*\]", format_str)
    if m0:
        raw_s, raw_e, raw_step = m0.group(1), m0.group(2), m0.group(3)
        start, end = int(raw_s), int(raw_e)
        step = int(raw_step) if raw_step else 1
        width = max(len(raw_s), len(raw_e))
        return [str(i).zfill(width) for i in range(start, end + 1, step)]

    # Range with prefix: [prefix, start...end(:step)]
    m1 = re.match(r"\[([^,\]]+),\s*([0-9A-Za-z:]+)\.\.\.([0-9A-Za-z:]+)(?::(\d+))?\]", format_str)
    if m1:
        prefix = m1.group(1).strip()
        raw_s, raw_e, raw_step = m1.group(2), m1.group(3), m1.group(4)
        step = int(raw_step) if raw_step else 1

        # Numeric range with zero-padding
        if raw_s.isdigit():
            start, end = int(raw_s), int(raw_e)
            width = max(len(raw_s), len(raw_e))
            return [f"{prefix}{str(i).zfill(width)}" for i in range(start, end + 1, step)]

        # Time range HH:MM...HH:MM
        if ":" in raw_s:
            s = datetime.strptime(raw_s, "%H:%M")
            e = datetime.strptime(raw_e, "%H:%M")
            out = []
            while s <= e:
                out.append(f"{prefix}{s.strftime('%H:%M')}")
                s += timedelta(hours=step)
            return out

        # Letter range
        if len(raw_s) == 1 and raw_s.isalpha():
            alpha = string.ascii_uppercase if raw_s.isupper() else string.ascii_lowercase
            segment = alpha[alpha.index(raw_s):alpha.index(raw_e) + 1:step]
            return [f"{prefix}{c}" for c in segment]

    # Grid template {A...C}{1...2}
    braces = re.findall(r"\{(.*?)\}", format_str)
    if braces:
        lists = []
        for expr in braces:
            a, b = expr.split("...")
            if a.isdigit():
                lists.append([str(i) for i in range(int(a), int(b) + 1)])
            else:
                alpha = string.ascii_uppercase if a.isupper() else string.ascii_lowercase
                lists.append(alpha[alpha.index(a):alpha.index(b) + 1])
        out = []
        for combo in product(*lists):
            tmp = format_str
            for v in combo:
                tmp = re.sub(r"\{.*?\}", v, tmp, count=1)
            out.append(tmp)
        return out

    # Simple list [A, B, C]
    if format_str.startswith("[") and format_str.endswith("]"):
        inner = format_str[1:-1]
        parts = [x.strip() for x in inner.split(",")]
        if len(parts) > 1:
            return parts

    # Fallback: return as-is
    return [format_str]

async def parse_permissions(perm_string: str, guild: discord.Guild):
    """Parse a permissions string into a dict of PermissionOverwrite objects."""
    overwrites = {}
    for entry in perm_string.split(","):
        if ":" not in entry:
            continue
        target_raw, key_raw = entry.split(":", 1)
        perm_key = key_raw.strip().lower()
        attr = PERMISSION_MAP.get(perm_key)
        if not attr:
            continue

        allow = discord.Permissions()
        setattr(allow, attr, True)
        deny = discord.Permissions()

        targ = target_raw.strip()
        obj = None
        # Role mention or name
        if targ.startswith("@"):
            name = targ[1:]
            obj = discord.utils.get(guild.roles, name=name)
            if not obj and name.isdigit():
                obj = discord.utils.get(guild.roles, id=int(name))
        # User ID
        elif targ.isdigit():
            try:
                obj = await guild.fetch_member(int(targ))
            except:
                obj = None

        if obj:
            overwrites[obj] = discord.PermissionOverwrite.from_pair(allow, deny)

    return overwrites

def admin_only():
    """Check that the user has administrator permissions."""
    return app_commands.checks.has_permissions(administrator=True)

# create_channels command
@bot.tree.command(name="create_channels", description="Create channels using a template and apply permissions")
@admin_only()
@app_commands.describe(
    template="Template string, e.g. [Room,1...5] or Sector{A...C}{1...2}",
    category_name="Category name (optional)",
    channel_type="Type: text, voice, forum, announcement, stage",
    permissions="Permissions string, e.g. @role:view, userID:connect"
)
async def create_channels(
    interaction: discord.Interaction,
    template: str,
    category_name: str = None,
    channel_type: str = "text",
    permissions: str = None
):
    await interaction.response.defer(ephemeral=True)

    names = interpret_template_string(template)
    guild = interaction.guild

    category = None
    if category_name:
        category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            category = await guild.create_category(name=category_name)

    type_map = {
        "text": discord.ChannelType.text,
        "voice": discord.ChannelType.voice,
        "forum": discord.ChannelType.forum,
        "announcement": discord.ChannelType.news,
        "stage": discord.ChannelType.stage_voice
    }
    ctype = type_map.get(channel_type.lower(), discord.ChannelType.text)

    overwrites = await parse_permissions(permissions, guild) if permissions else None

    created = []
    for nm in names:
        kwargs = {"name": nm, "category": category}
        if overwrites:
            kwargs["overwrites"] = overwrites
        try:
            if ctype == discord.ChannelType.text:
                ch = await guild.create_text_channel(**kwargs)
            elif ctype == discord.ChannelType.voice:
                ch = await guild.create_voice_channel(**kwargs)
            elif ctype == discord.ChannelType.stage_voice:
                ch = await guild.create_stage_channel(**kwargs)
            elif ctype == discord.ChannelType.news:
                ch = await guild.create_text_channel(**{**kwargs, "news": True})
            elif ctype == discord.ChannelType.forum:
                ch = await guild.create_forum_channel(**kwargs)
            created.append(ch.name)
        except Exception as e:
            print(f"Failed to create channel {nm}: {e}")

    await interaction.followup.send(
        f"✅ Created {len(created)} channels: {', '.join(created)}",
        ephemeral=True
    )

# remove_channels command
@bot.tree.command(name="remove_channels", description="Remove channels by template or category")
@admin_only()
@app_commands.describe(
    template="[1...3, if i % 2 == 0]",
    category_name="Category name or ID"
)
async def remove_channels(
    interaction: discord.Interaction,
    template: str = None,
    category_name: str = None
):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    deleted = []

    if category_name:
        category_name = category_name.strip()
        if category_name.isdigit():
            category = discord.utils.get(guild.categories, id=int(category_name))
        else:
            category = discord.utils.get(guild.categories, name=category_name)
        if not category:
            return await interaction.followup.send(f"❌ Category `{category_name}` not found", ephemeral=True)

    if template:
        tpl = template.strip()
        condition = None
        m = re.match(r"^\[(.*)\]$", tpl)
        if m:
            inner = m.group(1)
            parts = inner.split(", if ", 1)
            range_part = parts[0].strip()
            if len(parts) > 1:
                condition = parts[1].strip()
            tpl = f"[{range_part}]"
        names = interpret_template_string(tpl)
        pool = category.channels if category_name else guild.channels
        for i, name in enumerate(names, start=1):
            if condition:
                try:
                    if not eval(condition):
                        continue
                except:
                    continue
            ch = discord.utils.get(pool, name=name)
            if ch:
                try:
                    await ch.delete()
                    deleted.append(name)
                except:
                    pass
        return await interaction.followup.send(f"🗑 Deleted {len(deleted)} channels", ephemeral=True)

    if category_name and not template:
        for ch in category.channels:
            await ch.delete()
            deleted.append(ch.name)
        return await interaction.followup.send(f"🗑 Deleted {len(deleted)} from `{category.name}`", ephemeral=True)

    await interaction.followup.send("ℹ️ Specify `template` and/or `category_name`", ephemeral=True)

# preview_template command
@bot.tree.command(name="preview_template", description="Preview channel names from a template")
@admin_only()
@app_commands.describe(template="Template string, optionally with if")
async def preview_template(interaction: discord.Interaction, template: str):
    await interaction.response.defer(ephemeral=True)
    preview = []
    try:
        if "if" in template:
            cond = re.search(r"if (.+)", template)
            expr = cond.group(1).strip() if cond else None
            base = re.sub(r",?\s*if .+", "", template).strip()
            for i, nm in enumerate(interpret_template_string(base), start=1):
                if expr and eval(expr):
                    preview.append(nm)
        else:
            preview = interpret_template_string(template)

        if not preview:
            return await interaction.followup.send("ℹ️ No results generated", ephemeral=True)

        lines = preview[:25]
        text = "\n".join(f"{idx}. `{name}`" for idx, name in enumerate(lines, start=1))
        extra = f"\n...and {len(preview) - 25} more" if len(preview) > 25 else ""
        await interaction.followup.send(f"📋 Preview:\n{text}{extra}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# GUI modal for create_channels
class TemplateModal(discord.ui.Modal, title="Create channels from template"):
    template = discord.ui.TextInput(label="Template", placeholder="[Room,1...5]")
    category = discord.ui.TextInput(label="Category", required=False)
    channel_type = discord.ui.TextInput(label="Channel type", placeholder="text, voice...", default="text")
    permissions = discord.ui.TextInput(label="Permissions", placeholder="@role:view, userID:connect", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admins only", ephemeral=True)
        cat = self.category.value.strip() or None
        perms = self.permissions.value.strip() or None
        await create_channels.callback(interaction, self.template.value, cat, self.channel_type.value, perms)

@bot.tree.command(name="gui_create", description="Open GUI to create channels")
@admin_only()
async def gui_create(interaction: discord.Interaction):
    await interaction.response.send_modal(TemplateModal())

# clone_category command
@bot.tree.command(name="clone_category", description="Clone category structure, supports templates")
@admin_only()
@app_commands.describe(source="Source category name or ID", target="Target name or template, e.g. 🎓 [3...4] suffix")
async def clone_category(interaction: discord.Interaction, source: str, target: str):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    # Locate source category by ID or name
    src = None
    if source.isdigit():
        src = discord.utils.get(guild.categories, id=int(source))
    if not src:
        src = discord.utils.get(guild.categories, name=source)
    if not src:
        return await interaction.followup.send(f"❌ Source `{source}` not found", ephemeral=True)

    # Extract template and prefix/suffix
    m = re.search(r"\[[^\]]+\]", target)
    if m:
        tpl = m.group(0)
        prefix = target[:m.start()]
        suffix = target[m.end():]
        bases = interpret_template_string(tpl)
        targets = [f"{prefix}{b}{suffix}" for b in bases]
    else:
        targets = [target]

    results = []
    for new_name in targets:
        new_cat = await guild.create_category(name=new_name, overwrites=src.overwrites)
        count = 0
        for ch in src.channels:
            kwargs = {"name": ch.name, "category": new_cat, "overwrites": ch.overwrites}
            try:
                if isinstance(ch, discord.TextChannel):
                    await guild.create_text_channel(**kwargs)
                elif isinstance(ch, discord.VoiceChannel):
                    await guild.create_voice_channel(**kwargs)
                elif isinstance(ch, discord.StageChannel):
                    await guild.create_stage_channel(**kwargs)
                elif isinstance(ch, discord.ForumChannel):
                    await guild.create_forum_channel(**kwargs)
                count += 1
            except Exception as e:
                print(f"Failed to clone {ch.name}: {e}")
        results.append((new_name, count))

    response = "\n".join(f"• `{n}` → {c} channels" for n, c in results)
    await interaction.followup.send(f"✅ Clone complete:\n{response}", ephemeral=True)

# Sync commands on ready
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot is ready as {bot.user}")

# Run the bot
bot.run(TOKEN)