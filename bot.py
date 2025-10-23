import os, time, asyncio, datetime as dt
from aiogram import Bot, types
from aiogram.dispatcher import Dispatcher
from aiogram.utils import executor

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Boolean
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func

# ===============================
# CONFIGURAÇÕES PRINCIPAIS
# ===============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or CHANNEL_ID == 0:
    raise RuntimeError("⚠️ Defina BOT_TOKEN e TELEGRAM_CHANNEL_ID nas variáveis de ambiente.")
if not DATABASE_URL:
    raise RuntimeError("⚠️ Defina DATABASE_URL com a string de conexão Postgres (Neon).")

# ===============================
# CONEXÕES
# ===============================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# ===============================
# MODELOS DO BANCO
# ===============================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, index=True, nullable=False)
    username = Column(String)
    created_at = Column(DateTime, server_default=func.now())
    subscriptions = relationship("Subscription", back_populates="user")
    invites = relationship("Invite", back_populates="user")

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="active")  # active / expired
    start_at = Column(DateTime, default=func.now())
    end_at = Column(DateTime, nullable=True)
    user = relationship("User", back_populates="subscriptions")

class Invite(Base):
    __tablename__ = "invites"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    invite_link = Column(String, unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    used_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    user = relationship("User", back_populates="invites")

Base.metadata.create_all(engine)

# ===============================
# FUNÇÕES DE BANCO
# ===============================

def get_or_create_user(tg_user: types.User):
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(telegram_id=tg_user.id).first()
        if not u:
            u = User(telegram_id=tg_user.id, username=tg_user.username)
            db.add(u); db.commit(); db.refresh(u)
        return u
    finally:
        db.close()

def get_active_subscription(user_id: int):
    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        return db.query(Subscription).filter(
            Subscription.user_id == user_id,
            Subscription.status == "active",
            Subscription.end_at != None,
            Subscription.end_at > now
        ).order_by(Subscription.end_at.desc()).first()
    finally:
        db.close()

def activate_subscription(user_id: int, days: int = 1):
    """Cria assinatura temporária (fase de testes, depois substituímos por pagamento PIX)."""
    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        sub = Subscription(
            user_id=user_id,
            status="active",
            start_at=now,
            end_at=now + dt.timedelta(days=days)
        )
        db.add(sub); db.commit()
    finally:
        db.close()

async def create_one_time_invite(user: User, minutes_valid: int = 60) -> str:
    expire_ts = int(time.time()) + minutes_valid * 60
    invite = await bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        name=f"assinatura:{user.telegram_id}",
        expire_date=expire_ts,
        member_limit=1
    )
    db = SessionLocal()
    try:
        inv = Invite(
            user_id=user.id,
            invite_link=invite.invite_link,
            expires_at=dt.datetime.utcnow() + dt.timedelta(minutes=minutes_valid),
            used=False
        )
        db.add(inv); db.commit()
    finally:
        db.close()
    return invite.invite_link

def mark_invite_used(invite_link: str, used_by_telegram_id: int):
    db = SessionLocal()
    try:
        inv = db.query(Invite).filter_by(invite_link=invite_link, used=False).first()
        if inv:
            inv.used = True
            inv.used_by = used_by_telegram_id
            db.commit()
    finally:
        db.close()

def get_pending_invite_link_for_user(user_id: int):
    db = SessionLocal()
    try:
        now = dt.datetime.utcnow()
        inv = db.query(Invite).filter(
            Invite.user_id == user_id,
            Invite.used == False,
            Invite.expires_at > now
        ).order_by(Invite.created_at.desc()).first()
        return inv.invite_link if inv else None
    finally:
        db.close()

# ===============================
# COMANDOS DO BOT
# ===============================

@dp.message_handler(commands=["start"])
async def cmd_start(msg: types.Message):
    u = get_or_create_user(msg.from_user)
    txt = (
        "👋 Bem-vindo!\n"
        "Este bot gerencia o acesso ao canal VIP por assinatura.\n\n"
        "Comandos disponíveis:\n"
        "• /status – ver sua assinatura\n"
        "• /entrar – gerar convite único (temporário)"
    )
    await msg.answer(txt)

@dp.message_handler(commands=["status"])
async def cmd_status(msg: types.Message):
    u = get_or_create_user(msg.from_user)
    sub = get_active_subscription(u.id)
    if sub:
        vence = sub.end_at.strftime("%d/%m/%Y %H:%M UTC")
        await msg.answer(f"✅ Assinatura *ativa*\nVence em: {vence}", parse_mode="Markdown")
    else:
        await msg.answer("ℹ️ Você ainda não possui assinatura ativa.\nUse /entrar para solicitar acesso.")

@dp.message_handler(commands=["entrar"])
async def cmd_entrar(msg: types.Message):
    u = get_or_create_user(msg.from_user)

    # 1️⃣ Verifica se já existe convite válido (para evitar duplicação)
    existing = get_pending_invite_link_for_user(u.id)
    if existing:
        await msg.answer("🔐 Seu convite ainda está válido (expira em até 60 min):\n" + existing)
        return

    # 2️⃣ Ativa assinatura de teste (futura versão: ativação via PIX)
    if not get_active_subscription(u.id):
        activate_subscription(u.id, days=1)

    # 3️⃣ Cria novo convite e envia
    link = await create_one_time_invite(u, minutes_valid=60)
    await msg.answer(
        "🔐 Seu convite (uso único, expira em 60 min):\n" + link +
        "\n\n⚠️ Não compartilhe. Se outra pessoa usar antes de você, será bloqueada."
    )

# ===============================
# MONITORAMENTO DE ENTRADAS NO CANAL
# ===============================

@dp.chat_member_handler()
async def on_chat_member(update: types.ChatMemberUpdated):
    if update.chat.id != CHANNEL_ID:
        return

    if update.new_chat_member.status == "member":
        joined_id = update.from_user.id
        db = SessionLocal()
        try:
            user = db.query(User).filter_by(telegram_id=joined_id).first()
        finally:
            db.close()

        if not user:
            try:
                await bot.ban_chat_member(CHANNEL_ID, joined_id)
                await bot.unban_chat_member(CHANNEL_ID, joined_id)
            except Exception:
                pass
            return

        pending_link = get_pending_invite_link_for_user(user.id)
        if pending_link:
            mark_invite_used(pending_link, used_by_telegram_id=joined_id)
            await bot.send_message(joined_id, "🎉 Acesso concedido! Bem-vindo ao canal.")
        else:
            try:
                await bot.ban_chat_member(CHANNEL_ID, joined_id)
                await bot.unban_chat_member(CHANNEL_ID, joined_id)
            except Exception:
                pass

# ===============================
# ROTINA DE EXPIRAÇÃO
# ===============================

async def expire_loop():
    while True:
        now = dt.datetime.utcnow()
        db = SessionLocal()
        try:
            expiring = db.query(Subscription).filter(
                Subscription.status == "active",
                Subscription.end_at < now
            ).all()
            for s in expiring:
                s.status = "expired"
                db.commit()
                user = db.query(User).filter_by(id=s.user_id).first()
                if user:
                    try:
                        await bot.ban_chat_member(CHANNEL_ID, user.telegram_id)
                        await bot.unban_chat_member(CHANNEL_ID, user.telegram_id)
                        await bot.send_message(user.telegram_id, "🔔 Sua assinatura expirou. Renove para continuar no canal.")
                    except Exception:
                        pass
        finally:
            db.close()
        await asyncio.sleep(1800)

# ===============================
# STARTUP
# ===============================

async def on_startup(dp: Dispatcher):
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(expire_loop())

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
