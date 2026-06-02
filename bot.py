import os
import re
import uuid
import time
import json
import asyncio
import logging
from pathlib import Path
from collections import defaultdict, deque
from urllib.parse import urlparse
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from openai import OpenAI
import yt_dlp

# =========================
# Configuração inicial
# =========================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-5-mini")
BOT_PREFIX = os.getenv("BOT_PREFIX", "!")
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "25"))
MAX_AUDIO_MINUTES = int(os.getenv("MAX_AUDIO_MINUTES", "20"))
GUILD_ID = os.getenv("GUILD_ID")

TEST_GUILD = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN não configurado no .env")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY não configurado no .env")

DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

LIBRARY_DIR = Path("audio_library")
LIBRARY_DIR.mkdir(exist_ok=True)

AUDIO_DB_FILE = Path("audio_db.json")
if not AUDIO_DB_FILE.exists():
    AUDIO_DB_FILE.write_text("{}", encoding="utf-8")

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("discord-gpt-bot")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.voice_states = True

bot = commands.Bot(command_prefix=BOT_PREFIX, intents=intents)

# =========================
# Estado em memória
# =========================
conversation_memory = defaultdict(lambda: deque(maxlen=8))
guild_locks = defaultdict(asyncio.Lock)


class AudioItem:
    def __init__(
        self,
        url: Optional[str] = None,
        requested_by: str = "",
        local_path: Optional[str] = None,
        title: Optional[str] = None,
        is_library_file: bool = False
    ):
        self.url = url
        self.requested_by = requested_by
        self.local_path = local_path
        self.title = title
        self.duration = None
        self.filesize = None
        self.id = str(uuid.uuid4())[:8]
        self.is_library_file = is_library_file


class GuildAudioState:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.now_playing = None
        self.player_task = None
        self.text_channel_id = None
        self.last_activity = time.time()

    def touch(self):
        self.last_activity = time.time()


audio_states = defaultdict(GuildAudioState)

# =========================
# Helpers de biblioteca de áudio
# =========================
def load_audio_db():
    try:
        return json.loads(AUDIO_DB_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Falha ao ler audio_db.json: %s", e)
        return {}


def save_audio_db(db):
    AUDIO_DB_FILE.write_text(
        json.dumps(db, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def normalize_alias(alias: str) -> str:
    alias = alias.strip().lower()
    alias = re.sub(r"[^a-zA-Z0-9_\-]", "_", alias)
    alias = re.sub(r"_+", "_", alias)
    return alias.strip("_")


def get_extension_from_name(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    allowed = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus", ".webm", ".mp4"}
    return ext if ext in allowed else ""


def get_audio_by_alias(alias: str):
    db = load_audio_db()
    alias = normalize_alias(alias)
    item = db.get(alias)
    if not item:
        return None

    file_path = LIBRARY_DIR / item["filename"]
    if not file_path.exists():
        return None

    result = dict(item)
    result["alias"] = alias
    result["path"] = str(file_path)
    return result


def list_audio_aliases():
    return load_audio_db()


def remove_audio_alias(alias: str):
    db = load_audio_db()
    alias = normalize_alias(alias)

    if alias not in db:
        return False, "Alias não encontrado."

    filename = db[alias]["filename"]
    file_path = LIBRARY_DIR / filename

    try:
        if file_path.exists():
            file_path.unlink()
    except Exception as e:
        return False, "Não consegui remover o arquivo físico: {0}".format(e)

    del db[alias]
    save_audio_db(db)
    return True, "Áudio removido com sucesso."


async def save_attachment_to_library(
    attachment: discord.Attachment,
    alias: str,
    uploaded_by: str
):
    db = load_audio_db()
    alias = normalize_alias(alias)

    if not alias:
        raise RuntimeError("Alias inválido.")

    ext = get_extension_from_name(attachment.filename)
    if not ext:
        raise RuntimeError(
            "Formato não suportado. Envie mp3, wav, ogg, m4a, aac, flac, opus, webm ou mp4."
        )

    if attachment.size and attachment.size > MAX_AUDIO_MB * 1024 * 1024:
        raise RuntimeError("Arquivo muito grande. Limite atual: {0} MB.".format(MAX_AUDIO_MB))

    filename = "{0}{1}".format(alias, ext)
    save_path = LIBRARY_DIR / filename

    await attachment.save(save_path)

    db[alias] = {
        "filename": filename,
        "original_name": attachment.filename,
        "uploaded_by": uploaded_by,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_audio_db(db)

    return alias, save_path, db[alias]


async def save_url_to_library(url: str, alias: str, uploaded_by: str):
    db = load_audio_db()
    alias = normalize_alias(alias)

    if not alias:
        raise RuntimeError("Alias inválido.")

    if not is_valid_url(url):
        raise RuntimeError("URL inválida.")

    info = await fetch_audio_info(url)
    validate_audio_constraints(info)

    temp_item = AudioItem(url=url, requested_by=uploaded_by)
    temp_item = await download_audio(temp_item)

    source_path = Path(temp_item.local_path)
    ext = source_path.suffix.lower()
    allowed = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus", ".webm", ".mp4"}

    if ext not in allowed:
        ext = ".mp3"

    filename = "{0}{1}".format(alias, ext)
    save_path = LIBRARY_DIR / filename

    if save_path.exists():
        save_path.unlink()

    source_path.replace(save_path)

    db[alias] = {
        "filename": filename,
        "original_name": info.get("title", source_path.name),
        "uploaded_by": uploaded_by,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_url": url
    }
    save_audio_db(db)

    return alias, save_path, db[alias]


# =========================
# Helpers gerais
# =========================
def is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def split_message(text: str, max_len: int = 1900):
    for i in range(0, len(text), max_len):
        yield text[i:i + max_len]


def safe_remove(path: Optional[str]):
    if not path:
        return
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.warning("Falha ao remover arquivo %s: %s", path, e)


def ffmpeg_executable() -> str:
    return "ffmpeg"


async def ensure_voice(interaction: discord.Interaction) -> discord.VoiceClient:
    if not interaction.guild:
        raise RuntimeError("Esse comando só funciona em servidor.")

    user_voice = getattr(interaction.user, "voice", None)
    if not user_voice or not user_voice.channel:
        raise RuntimeError("Você precisa estar em um canal de voz.")

    target_channel = user_voice.channel
    voice_client = interaction.guild.voice_client

    if voice_client and voice_client.channel.id != target_channel.id:
        await voice_client.move_to(target_channel)
        return voice_client

    if not voice_client:
        voice_client = await target_channel.connect()

    return voice_client


async def get_ai_response(channel_id: int, user_message: str) -> str:
    memory = conversation_memory[channel_id]
    memory.append({"role": "user", "content": user_message})

    input_items = [
        {
            "role": "system",
            "content": (
                "Você é um assistente útil dentro do Discord. "
                "Responda em português do Brasil, com clareza, objetividade e naturalidade. "
                "Se a pergunta estiver ambígua, diga sua melhor interpretação."
            ),
        }
    ]

    for item in memory:
        input_items.append(item)

    response = openai_client.responses.create(
        model=GPT_MODEL,
        input=input_items
    )

    text = getattr(response, "output_text", None) or "Não consegui responder agora."
    memory.append({"role": "assistant", "content": text})
    return text


async def fetch_audio_info(url: str) -> dict:
    loop = asyncio.get_running_loop()

    def _extract():
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    return await loop.run_in_executor(None, _extract)


async def download_audio(item: AudioItem) -> AudioItem:
    loop = asyncio.get_running_loop()
    outtmpl = str(DOWNLOAD_DIR / "%(title).70s_{0}.%(ext)s".format(item.id))

    def _download():
        opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(item.url, download=True)
            local_path = ydl.prepare_filename(info)
            return info, local_path

    info, path = await loop.run_in_executor(None, _download)

    item.local_path = path
    item.title = info.get("title", Path(path).name)
    item.duration = info.get("duration")
    item.filesize = info.get("filesize") or info.get("filesize_approx")
    return item


def validate_audio_constraints(info: dict):
    duration = info.get("duration")
    filesize = info.get("filesize") or info.get("filesize_approx")

    if duration and duration > MAX_AUDIO_MINUTES * 60:
        raise RuntimeError(
            "Áudio muito longo. Limite atual: {0} minutos.".format(MAX_AUDIO_MINUTES)
        )

    if filesize and filesize > MAX_AUDIO_MB * 1024 * 1024:
        raise RuntimeError(
            "Arquivo muito grande. Limite atual: {0} MB.".format(MAX_AUDIO_MB)
        )


async def send_followup_text(interaction: discord.Interaction, text: str):
    if interaction.response.is_done():
        await interaction.followup.send(text)
    else:
        await interaction.response.send_message(text)


# =========================
# Player de fila
# =========================
async def player_loop(guild_id: int):
    state = audio_states[guild_id]
    logger.info("Player iniciado para guild %s", guild_id)

    while True:
        try:
            item = await state.queue.get()
            state.now_playing = item
            state.touch()

            guild = bot.get_guild(guild_id)
            if guild is None:
                logger.warning("Guild %s não encontrada", guild_id)
                continue

            voice_client = guild.voice_client
            if not voice_client or not voice_client.is_connected():
                logger.warning("Sem voice client conectado na guild %s", guild_id)
                continue

            if not item.local_path:
                item = await download_audio(item)

            done = asyncio.Event()

            def after_playback(error):
                if error:
                    logger.error("Erro na reprodução: %s", error)
                bot.loop.call_soon_threadsafe(done.set)

            source = discord.FFmpegPCMAudio(
                executable=ffmpeg_executable(),
                source=item.local_path,
                options="-vn"
            )

            voice_client.play(source, after=after_playback)

            if state.text_channel_id:
                channel = bot.get_channel(state.text_channel_id)
                if channel:
                    try:
                        await channel.send(
                            "🎵 Tocando agora: **{0}**\n"
                            "👤 Pedido por: **{1}**".format(
                                item.title or "Áudio sem título",
                                item.requested_by
                            )
                        )
                    except Exception as e:
                        logger.warning("Falha ao avisar canal: %s", e)

            await done.wait()
            # se não tem mais nada na fila, sai da call
            if state.queue.empty():
                guild = bot.get_guild(guild_id)
                if guild and guild.voice_client:
                    try:
                        await guild.voice_client.disconnect()
                    except Exception as e:
                        logger.warning(f"Erro ao sair automaticamente: {e}")
                        
            if not item.is_library_file:
                safe_remove(item.local_path)

            state.now_playing = None
            state.touch()

        except Exception as e:
            logger.exception("Erro no player_loop da guild %s: %s", guild_id, e)
            if state.now_playing:
                if not getattr(state.now_playing, "is_library_file", False):
                    safe_remove(state.now_playing.local_path)
                state.now_playing = None


# =========================
# Eventos
# =========================
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if bot.user and bot.user in message.mentions:
        content = re.sub(r"<@!?{0}>".format(bot.user.id), "", message.content).strip()
        if content:
            try:
                reply = await get_ai_response(message.channel.id, content)
                for chunk in split_message(reply):
                    await message.channel.send(chunk)
            except Exception as e:
                logger.exception("Erro ao responder menção: %s", e)
                await message.channel.send("Erro ao consultar a IA: {0}".format(e))

    await bot.process_commands(message)


# =========================
# Slash commands
# =========================
@bot.tree.command(name="entrar", description="Faz o bot entrar no seu canal de voz")
async def entrar(interaction: discord.Interaction):
    try:
        await ensure_voice(interaction)
        await interaction.response.send_message("Entrei na call.")
    except Exception as e:
        logger.exception("Erro no /entrar")
        await interaction.response.send_message("Não consegui entrar: {0}".format(e), ephemeral=True)


@bot.tree.command(name="sair", description="Faz o bot sair do canal de voz")
async def sair(interaction: discord.Interaction, silent: bool = False):
    try:
        if not interaction.guild or not interaction.guild.voice_client:
            if not silent:
                await interaction.response.send_message("Não estou em nenhuma call.", ephemeral=True)
            return

        state = audio_states[interaction.guild.id]

        while not state.queue.empty():
            try:
                queued = state.queue.get_nowait()
                if not getattr(queued, "is_library_file", False):
                    safe_remove(getattr(queued, "local_path", None))
            except Exception:
                break

        vc = interaction.guild.voice_client

        if vc.is_playing():
            vc.stop()

        await vc.disconnect()

        if not silent:
            await interaction.response.send_message("Saí da call e limpei a fila.")

    except Exception as e:
        logger.exception("Erro no /sair")
        if not silent:
            await interaction.response.send_message(f"Erro ao sair: {e}", ephemeral=True)


@bot.tree.command(name="tocar", description="Adiciona um áudio por URL na fila")
@app_commands.describe(url="URL do áudio ou vídeo")
async def tocar(interaction: discord.Interaction, url: str):
    if not is_valid_url(url):
        await interaction.response.send_message("URL inválida.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    try:
        async with guild_locks[interaction.guild.id]:
            await ensure_voice(interaction)
            state = audio_states[interaction.guild.id]
            state.text_channel_id = interaction.channel_id
            state.touch()

            info = await fetch_audio_info(url)
            validate_audio_constraints(info)

            item = AudioItem(
                url=url,
                requested_by=interaction.user.display_name
            )
            item.title = info.get("title", "Áudio sem título")
            item.duration = info.get("duration")
            item.filesize = info.get("filesize") or info.get("filesize_approx")

            await state.queue.put(item)

            if not state.player_task or state.player_task.done():
                state.player_task = asyncio.create_task(player_loop(interaction.guild.id))

            queue_size = state.queue.qsize()
            await interaction.followup.send(
                "Adicionado à fila: **{0}**\n"
                "Posição aproximada: **{1}**".format(item.title, queue_size)
            )
    except Exception as e:
        logger.exception("Erro no /tocar")
        await interaction.followup.send("Erro ao adicionar áudio: {0}".format(e))


@bot.tree.command(name="adicionar", description="Salva um áudio por arquivo ou URL com um alias")
@app_commands.describe(
    alias="Nome curto para reutilizar o áudio",
    url="URL do áudio ou vídeo",
    arquivo="Arquivo de áudio enviado no Discord"
)
async def adicionar(
    interaction: discord.Interaction,
    alias: str,
    url: Optional[str] = None,
    arquivo: Optional[discord.Attachment] = None
):
    await interaction.response.defer(thinking=True)

    try:
        alias = normalize_alias(alias)

        if not alias:
            await interaction.followup.send("Alias inválido.")
            return

        if url and arquivo:
            await interaction.followup.send("Informe apenas um dos dois: `url` ou `arquivo`.")
            return

        if not url and not arquivo:
            await interaction.followup.send("Você precisa enviar um `arquivo` ou informar uma `url`.")
            return

        if arquivo is not None:
            alias, save_path, meta = await save_attachment_to_library(
                attachment=arquivo,
                alias=alias,
                uploaded_by=interaction.user.display_name
            )

            await interaction.followup.send(
                "Áudio salvo com sucesso por upload.\n"
                "**Alias:** `{0}`\n"
                "**Arquivo:** `{1}`".format(alias, meta["filename"])
            )
            return

        alias, save_path, meta = await save_url_to_library(
            url=url,
            alias=alias,
            uploaded_by=interaction.user.display_name
        )

        await interaction.followup.send(
            "Áudio salvo com sucesso por URL.\n"
            "**Alias:** `{0}`\n"
            "**Arquivo:** `{1}`".format(alias, meta["filename"])
        )

    except Exception as e:
        logger.exception("Erro no /adicionar")
        await interaction.followup.send("Erro ao salvar áudio: {0}".format(e))


@bot.tree.command(name="falar", description="Toca um áudio salvo por alias")
@app_commands.describe(alias="Alias do áudio salvo")
async def falar(interaction: discord.Interaction, alias: str):
    await interaction.response.defer(thinking=True)

    try:
        item_data = get_audio_by_alias(alias)
        if not item_data:
            await interaction.followup.send("Alias não encontrado.")
            return

        async with guild_locks[interaction.guild.id]:
            await ensure_voice(interaction)
            state = audio_states[interaction.guild.id]
            state.text_channel_id = interaction.channel_id
            state.touch()

            item = AudioItem(
                requested_by=interaction.user.display_name,
                local_path=item_data["path"],
                title="{0} ({1})".format(item_data["alias"], item_data["original_name"]),
                is_library_file=True
            )

            await state.queue.put(item)

            if not state.player_task or state.player_task.done():
                state.player_task = asyncio.create_task(player_loop(interaction.guild.id))

            await interaction.followup.send(
                "Áudio `{0}` adicionado à fila.".format(item_data["alias"])
            )

    except Exception as e:
        logger.exception("Erro no /falar")
        await interaction.followup.send("Erro ao tocar alias: {0}".format(e))


@bot.tree.command(name="listar_audios", description="Lista os áudios salvos")
async def listar_audios(interaction: discord.Interaction):
    try:
        db = list_audio_aliases()
        if not db:
            await interaction.response.send_message("Nenhum áudio salvo.")
            return

        lines = []
        items = list(db.items())[:50]
        for alias, item in items:
            lines.append(
                "`{0}` → {1} (por {2})".format(
                    alias,
                    item.get("original_name", "arquivo desconhecido"),
                    item.get("uploaded_by", "desconhecido")
                )
            )

        text = "\n".join(lines)
        await interaction.response.send_message("**Áudios salvos:**\n{0}".format(text))
    except Exception as e:
        logger.exception("Erro no /listar_audios")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="remover_audio", description="Remove um áudio salvo")
@app_commands.describe(alias="Alias do áudio salvo")
async def remover_audio(interaction: discord.Interaction, alias: str):
    try:
        ok, msg = remove_audio_alias(alias)
        await interaction.response.send_message(msg, ephemeral=not ok)
    except Exception as e:
        logger.exception("Erro no /remover_audio")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="pause", description="Pausa a reprodução")
async def pause(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("Áudio pausado.")
        else:
            await interaction.response.send_message("Não há áudio tocando.", ephemeral=True)
    except Exception as e:
        logger.exception("Erro no /pause")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="resume", description="Retoma a reprodução")
async def resume(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Áudio retomado.")
        else:
            await interaction.response.send_message("Não há áudio pausado.", ephemeral=True)
    except Exception as e:
        logger.exception("Erro no /resume")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="skip", description="Pula o áudio atual")
async def skip(interaction: discord.Interaction):
    try:
        vc = interaction.guild.voice_client if interaction.guild else None
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message("Pulando o áudio atual.")
        else:
            await interaction.response.send_message("Não há áudio em reprodução.", ephemeral=True)
    except Exception as e:
        logger.exception("Erro no /skip")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="stop", description="Para o áudio e limpa a fila")
async def stop(interaction: discord.Interaction):
    try:
        if not interaction.guild:
            await interaction.response.send_message("Comando válido só em servidor.", ephemeral=True)
            return

        state = audio_states[interaction.guild.id]

        while not state.queue.empty():
            try:
                queued = state.queue.get_nowait()
                if not getattr(queued, "is_library_file", False):
                    safe_remove(getattr(queued, "local_path", None))
            except Exception:
                break

        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

        await interaction.response.send_message("Reprodução parada e fila limpa.")
    except Exception as e:
        logger.exception("Erro no /stop")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="fila", description="Mostra a fila atual")
async def fila(interaction: discord.Interaction):
    try:
        if not interaction.guild:
            await interaction.response.send_message("Comando válido só em servidor.", ephemeral=True)
            return

        state = audio_states[interaction.guild.id]
        current = state.now_playing.title if state.now_playing else "Nada tocando"

        queued_items = list(state.queue._queue)
        if queued_items:
            lines = []
            for i, item in enumerate(queued_items[:10]):
                label = item.title or item.url or "Áudio sem nome"
                lines.append("{0}. {1}".format(i + 1, label))
            text = "\n".join(lines)
        else:
            text = "Fila vazia."

        await interaction.response.send_message(
            "**Tocando agora:** {0}\n\n**Próximos:**\n{1}".format(current, text)
        )
    except Exception as e:
        logger.exception("Erro no /fila")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="gpt", description="Pergunta algo para a IA")
@app_commands.describe(pergunta="Sua pergunta")
async def gpt(interaction: discord.Interaction, pergunta: str):
    await interaction.response.defer(thinking=True)
    try:
        text = await get_ai_response(interaction.channel_id, pergunta)
        for chunk in split_message(text):
            await interaction.followup.send(chunk)
    except Exception as e:
        logger.exception("Erro no /gpt")
        await interaction.followup.send("Erro ao consultar a IA: {0}".format(e))


@bot.tree.command(name="limpar_contexto", description="Limpa a memória curta da IA neste canal")
async def limpar_contexto(interaction: discord.Interaction):
    try:
        conversation_memory[interaction.channel_id].clear()
        await interaction.response.send_message("Contexto deste canal limpo.")
    except Exception as e:
        logger.exception("Erro no /limpar_contexto")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


@bot.tree.command(name="status", description="Mostra status do bot")
async def status(interaction: discord.Interaction):
    try:
        guild_id = interaction.guild.id if interaction.guild else None
        state = audio_states[guild_id] if guild_id else None
        vc = interaction.guild.voice_client if interaction.guild else None

        queue_size = state.queue.qsize() if state else 0
        current = state.now_playing.title if state and state.now_playing else "Nada"
        connected = "Sim" if vc and vc.is_connected() else "Não"

        await interaction.response.send_message(
            "**Bot online**\n"
            "Conectado em voz: **{0}**\n"
            "Tocando: **{1}**\n"
            "Itens na fila: **{2}**\n"
            "Modelo GPT: **{3}**".format(
                connected,
                current,
                queue_size,
                GPT_MODEL
            )
        )
    except Exception as e:
        logger.exception("Erro no /status")
        await interaction.response.send_message("Erro: {0}".format(e), ephemeral=True)


# =========================
# Prefix command opcional
# =========================
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send("pong")


# =========================
# Limpeza periódica
# =========================
async def cleanup_task():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            now = time.time()
            for file_path in DOWNLOAD_DIR.glob("*"):
                try:
                    if file_path.is_file():
                        age = now - file_path.stat().st_mtime
                        if age > 3600:
                            file_path.unlink()
                except Exception as e:
                    logger.warning("Erro limpando arquivo %s: %s", file_path, e)
        except Exception as e:
            logger.warning("Erro na limpeza periódica: %s", e)

        await asyncio.sleep(600)


@bot.event
async def setup_hook():
    bot.loop.create_task(cleanup_task())

    try:
        synced = await bot.tree.sync()
        print("SYNC SETUP_HOOK:", [c.name for c in synced])
    except Exception as e:
        print("Erro no sync:", e)


bot.run(DISCORD_TOKEN)