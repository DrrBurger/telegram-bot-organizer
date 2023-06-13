import aiogram
import logging
import aiosqlite
from aiogram import Bot, types
from aiogram.utils import executor
from aiogram.dispatcher import Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from config_data.config import Config, load_config
from states.states import Place, Del
from aiogram.contrib.fsm_storage.memory import MemoryStorage

# Логирование бота
logging.basicConfig(level=logging.INFO)

config: Config = load_config()

input_data = {}
# Инициализация бота и диспетчера
bot = Bot(token=config.tg_bot.token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# Пример списка разрешенных ID пользователей
@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    await bot.send_message(message.chat.id, "Привет, я ваш бот!")


@dp.message_handler(commands=['help'])
async def help_command(message: types.Message):
    help_text = "Доступные команды:\n" \
                "/add - Добавить новое место\n" \
                "/del - Удалить место (только для администраторов)\n" \
                "/place - Вывести список всех мест" \
                "/random - Выбирате случайное место"
    await message.reply(help_text)


@dp.message_handler(Command('add'))
async def start_cmd_handler(message: types.Message):
    # начинаем диалог
    await message.answer("Введите название места:")
    await Place.name.set()


@dp.message_handler(state=Place.name)
async def process_name(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text
    await message.answer("Введите адрес места:")
    await Place.next()


@dp.message_handler(state=Place.address)
async def process_address(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['address'] = message.text
        # добавляем место в базу данных
        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('CREATE TABLE IF NOT EXISTS places (name text, address text)')
            await cursor.execute('INSERT INTO places (name, address) VALUES (?, ?)', (data['name'], data['address']))
            await db.commit()
    await message.answer("Место успешно добавлено!")
    await state.finish()


@dp.message_handler(Command('place'))
async def show_places(message: types.Message):
    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places')
        rows = await cursor.fetchall()
        for row in rows:
            await message.answer(f"Название: {row[0]}, Адрес: {row[1]}")


# Проверка, является ли пользователь администратором или доверенным лицом
async def admin_check(message: types.Message):
    chat_member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
    return chat_member.status in ["creator", "administrator"] or message.from_user.id in config.tg_bot.admin_ids


# Команда удаления
@dp.message_handler(Command('del'), state="*")
async def start_del_cmd_handler(message: types.Message):
    # проверяем, является ли пользователь администратором или находится ли его идентификатор в списке разрешенных
    if not await admin_check(message):
        return await message.answer("Вы не являетесь администратором или не имеете разрешения!")
    await message.answer("Введите название места, которое нужно удалить:")
    await Del.name.set()


@dp.message_handler(state=Del.name)
async def process_del_name(message: types.Message, state: FSMContext):
    # проверяем, является ли пользователь администратором
    if not await admin_check(message):
        return await message.answer("Вы не являетесь администратором!")
    async with state.proxy() as data:
        data['name'] = message.text
        # удаляем место из базы данных
        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('DELETE FROM places WHERE name = ?', (data['name'],))
            await db.commit()
    await message.answer("Место успешно удалено!")
    await state.finish()

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
