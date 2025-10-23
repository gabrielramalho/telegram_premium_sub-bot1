import os, time, asyncio, datetime as dt
from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.utils import executor

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))

if not BOT_TOKEN or CHANNEL_ID == 0:
    raise RuntimeError("Defina as variáveis de ambiente BOT_TOKEN e TELEGRAM_CHANNEL_ID.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)  # <<<<<<<<<<<<<< IMPORTANTE: passar o bot aqui

# --- memória simplificada (depois trocaremos por BD) ---
users = {}   # telegram_id -> {"status": "active|expired|none", "end_at": dt, "pending_invite": str|None}
invites = {} # invite_link -> {"expected_user_id": int, "expires_at": dt}

async def create_one_time_invite(expected_user_id: int, minutes_valid: int = 60) -> str:
    expire_ts = int(time.time()) + minutes_valid * 60
    invite = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name=f"assinatura:{expected_user_id}",
        expire_date=expire_ts,
        member_limit=1
    )
    invites[invite.invite_link] = {
        "expected_user_id": expected_user_id,
        "expires_at": dt.datetime.utcnow() + dt.timedelta(minutes=minutes_valid)
    }
    return invite.invite_link

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    uid = msg.from_user.id
    users.setdefault(uid, {"status": "none", "end_at": None, "pending_invite": None})
    txt = (
        "👋 Bem-vindo!\n"
        "Este bot gerencia acesso ao canal premium por assinatura.\n\n"
        "Comandos:\n"
        "• /status – ver sua assinatura\n"
        "• /entrar – gerar convite único (temporário)"
    )
    await msg.answer(txt)

@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message):
    u = users.get(msg.from_user.id, {"status":"none","end_at":None})
    if u["status"] == "active":
        vence = u["end_at"].strftime("%d/%m/%Y %H:%M") if u["end_at"] else "—"
        await msg.answer(f"✅ Assinatura *ativa*\nVence em: {vence}", parse_mode="Markdown")
    elif u["status"] == "expired":
        await msg.answer("⚠️ Sua assinatura está *expirada*\nUse /entrar após renovar.", parse_mode="Markdown")
    else:
        await msg.answer("ℹ️ Você ainda não possui assinatura ativa.\nUse /entrar para solicitar acesso.")

@dp.message_handler(commands=["entrar"])
async def cmd_entrar(msg: types.Message):
    uid = msg.from_user.id
    # Simulação: ativa assinatura por 1 dia (na próxima etapa, isso ocorrerá após PIX confirmado)
    users[uid] = {"status": "active", "end_at": dt.datetime.utcnow()+dt.timedelta(days=1), "pending_invite": None}
    invite_link = await create_one_time_invite(uid, minutes_valid=60)
    users[uid]["pending_invite"] = invite_link
    await msg.answer(
        "🔐 Seu convite (uso único, expira em 60 min):\n"
        + invite_link +
        "\n\n⚠️ Não compartilhe. Se outra pessoa usar antes de você, será bloqueada."
    )

@dp.chat_member_handler()
async def on_chat_member(update: types.ChatMemberUpdated):
    # Dispara quando alguém muda de status no chat (ex.: entrou)
    if update.chat.id != CHANNEL_ID:
        return
    if update.new_chat_member.status == "member":
        joined_id = update.from_user.id
        # Verifica se quem entrou é quem deveria usar o convite
        expected = users.get(joined_id, {}).get("pending_invite")
        if expected and expected in invites:
            # Dono correto do convite
            invites.pop(expected, None)
            users[joined_id]["pending_invite"] = None
            await bot.send_message(joined_id, "🎉 Acesso concedido! Bem-vindo ao canal.")
        else:
            # Não era o dono do convite: remove do canal e libera para reentrar no futuro
            try:
                await bot.ban_chat_member(CHANNEL_ID, joined_id)
                await bot.unban_chat_member(CHANNEL_ID, joined_id)
            except Exception:
                pass

async def expire_loop():
    # Verifica a cada 30 minutos se há assinaturas vencidas e remove do canal
    while True:
        now = dt.datetime.utcnow()
        for uid, data in list(users.items()):
            if data["status"] == "active" and data["end_at"] and data["end_at"] < now:
                data["status"] = "expired"
                try:
                    await bot.ban_chat_member(CHANNEL_ID, uid)
                    await bot.unban_chat_member(CHANNEL_ID, uid)
                    await bot.send_message(uid, "🔔 Sua assinatura expirou. Renove para continuar no canal.")
                except Exception:
                    pass
        await asyncio.sleep(1800)  # 30 minutos

async def on_startup(dp: Dispatcher):
    asyncio.create_task(expire_loop())

# ... imports e código acima mantidos ...

async def on_startup(dp: Dispatcher):
    # mata qualquer webhook pendente e descarta updates antigos
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(expire_loop())

if __name__ == "__main__":
    from aiogram.utils import executor
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)

