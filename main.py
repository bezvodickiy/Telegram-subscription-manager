import os
import logging
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import LabeledPrice, PreCheckoutQuery, InlineKeyboardButton, InlineKeyboardMarkup, ChatMemberUpdated
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, IS_MEMBER, LEFT, KICKED
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- CONFIGURATION ---
load_dotenv()
logging.basicConfig(level=logging.INFO)

CHANNEL_ID = -1003981483434 
ADMIN_ID = 8190360582
PAYMENT_TOKEN = os.getenv("PAYMENT_TOKEN")

# Google Sheets API
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_file = "credentials.json" 
creds = ServiceAccountCredentials.from_json_keyfile_name(creds_file, scope)
client = gspread.authorize(creds)
sheet = client.open("TEST PRIVATE CHANEL").sheet1 

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

# Plans: Name -> (Price in cents, Days)
TARIFS = {
    "plan_30": ("1 Month VIP Access", 1999, 30),
    "plan_90": ("3 Months VIP Access", 4999, 90),
    "plan_365": ("1 Year VIP Access", 14999, 365)
}

class Registration(StatesGroup):
    waiting_for_insta = State()
    waiting_for_name = State()

# --- SUBSCRIPTION AUTO-CHECK ---

async def check_subscriptions():
    logging.info("⏳ Starting scheduled subscription audit...")
    now = datetime.now()
    all_users = sheet.get_all_values()[1:] 
    
    for i, row in enumerate(all_users):
        try:
            row_index = i + 2
            user_id = row[1]
            expiry_date_str = row[6]
            status = row[7]
            
            if status == "✅ In Group":
                expiry_date = datetime.strptime(expiry_date_str, "%d.%m.%Y")
                
                if now > expiry_date:
                    try:
                        await bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=int(user_id))
                        await bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=int(user_id)) 
                        
                        await bot.send_message(user_id, "❌ Your subscription has expired. Please use /start to renew your access.")
                        sheet.update_cell(row_index, 8, "⌛ Expired")
                    except Exception as e:
                        logging.error(f"Error removing user {user_id}: {e}")
        except:
            continue

# --- HANDLERS ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    kb = [[types.KeyboardButton(text="💎 Subscription Plans")], [types.KeyboardButton(text="🔒 My Access")]]
    markup = types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    await message.answer("Welcome! Select a plan to join our VIP channel:", reply_markup=markup)

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    
    all_data = sheet.get_all_values()[1:]
    total = len(all_data)
    active = sum(1 for r in all_data if r[7] == "✅ In Group")
    
    revenue = 0
    for r in all_data:
        if "1 Month" in r[4]: revenue += 19.99
        elif "3 Months" in r[4]: revenue += 49.99
        elif "1 Year" in r[4]: revenue += 149.99

    await message.answer(
        f"📊 **ADMIN DASHBOARD**\n\n"
        f"👥 Total Sales: {total}\n"
        f"✅ Active Members: {active}\n"
        f"💰 Total Revenue: ${revenue:.2f}",
        parse_mode="Markdown"
    )

@dp.message(F.text == "💎 Subscription Plans")
async def show_plans(message: types.Message):
    buttons = [
        [InlineKeyboardButton(text="🥉 1 Month — $19.99", callback_data="plan_30")],
        [InlineKeyboardButton(text="🥈 3 Months — $49.99", callback_data="plan_90")],
        [InlineKeyboardButton(text="🥇 1 Year — $149.99", callback_data="plan_365")]
    ]
    await message.answer("Choose your plan:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("plan_"))
async def send_invoice(callback: types.CallbackQuery):
    name, price, days = TARIFS[callback.data]
    
    # Custom English Pay Button
    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Pay ${price/100:.2f}", pay=True)],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
    ])
    
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=name,
        description=f"VIP Access for {days} days",
        payload=callback.data,
        provider_token=PAYMENT_TOKEN,
        currency="USD",
        prices=[LabeledPrice(label=name, amount=price)],
        start_parameter="vip-sub",
        reply_markup=pay_kb
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def success_payment(message: types.Message, state: FSMContext):
    await state.update_data(plan_payload=message.successful_payment.invoice_payload)
    await message.answer("✅ Payment verified! Please enter your **Instagram** username:")
    await state.set_state(Registration.waiting_for_insta)

@dp.message(Registration.waiting_for_insta)
async def process_insta(message: types.Message, state: FSMContext):
    await state.update_data(insta=message.text)
    tg_username = message.from_user.username
    
    if tg_username:
        await finalize_registration(message, state, f"@{tg_username}")
    else:
        await message.answer("No Telegram username detected. Please enter your **Full Name**:")
        await state.set_state(Registration.waiting_for_name)

@dp.message(Registration.waiting_for_name)
async def process_manual_name(message: types.Message, state: FSMContext):
    await finalize_registration(message, state, message.text)

async def finalize_registration(message: types.Message, state: FSMContext, tg_info: str):
    data = await state.get_data()
    name_tarif, price, days = TARIFS[data['plan_payload']]
    expiry = (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")
    
    sheet.append_row([
        tg_info, str(message.from_user.id), data['insta'], 
        name_tarif, datetime.now().strftime("%d.%m.%Y %H:%M"), expiry, "✅ In Group"
    ])

    invite = await bot.create_chat_invite_link(chat_id=CHANNEL_ID, member_limit=1)
    await message.answer(f"🎉 Success! Access valid until: {expiry}\n\n🔗 [JOIN VIP CHANNEL]({invite.invite_link})", parse_mode="Markdown")
    await state.clear()

# --- CHANNEL MONITORING ---

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_MEMBER >> (LEFT | KICKED)))
async def on_user_left(event: ChatMemberUpdated):
    try:
        cell = sheet.find(str(event.from_user.id), in_column=2)
        if cell: sheet.update_cell(cell.row, 8, "❌ Left Group")
    except: pass

@dp.chat_member(ChatMemberUpdatedFilter(member_status_changed=(LEFT | KICKED) >> IS_MEMBER))
async def on_user_joined(event: ChatMemberUpdated):
    try:
        cell = sheet.find(str(event.from_user.id), in_column=2)
        if cell: sheet.update_cell(cell.row, 8, "✅ In Group")
    except: pass

# --- STARTUP ---
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_subscriptions, "cron", hour=0, minute=1)
    scheduler.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member", "successful_payment", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())