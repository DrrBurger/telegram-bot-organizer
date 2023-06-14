import asyncio
from collections import defaultdict
import logging
import random

from aiogram import Bot, types, exceptions
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import Dispatcher, FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.utils import executor

import aiosqlite

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config_data.config import Config, load_config

from states.states import Del, Place, Rating


# Логирование бота
logging.basicConfig(level=logging.INFO)

# загрузка конфига с данными
config: Config = load_config()

# Инициализация бота и диспетчера
bot = Bot(token=config.tg_bot.token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)


# хэндлер реагирующий на команду /start и /help
@dp.message_handler(Command(commands=['start', 'help']))
async def help_command(message: types.Message) -> None:
    if 'start' in message.text:
        await bot.send_message(message.chat.id, "Привет, я ваш бот!\nВсе команды - /help")
    else:
        help_text = "Доступные команды:\n" \
            "/add - Добавить новое место\n" \
            "/del - Удалить место (только для администраторов)\n" \
            "/place - Вывести список всех мест\n" \
            "/random - Выбрать случайное место\n" \
            "/rating - Поставить оценку выбранному месту\n" \
            "/poll - Отправляяет опрос с выбором дня"
        await message.answer(help_text)

    # Удаляем сообщение с командой от пользователя
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


# хэндлер реагирующий на команду /add
@dp.message_handler(Command('add'))
async def start_cmd_handler(message: types.Message) -> None:
    state = dp.current_state(chat=message.chat.id, user=message.from_user.id)
    # начинаем диалог
    bot_message = await message.answer("Введите название места:")
    await state.update_data(message_id=[message.message_id, bot_message.message_id])  # сохраняем идентификаторы сообщений
    await Place.name.set()


# состояние ввода 'места' вызванное после команды /add
@dp.message_handler(state=Place.name)
async def process_name(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text.lower()
        data['message_id'].extend([message.message_id])  # сохраняем идентификатор сообщения
    bot_message = await message.answer("Введите адрес места:")
    await state.update_data(message_id=data['message_id'] + [bot_message.message_id])
    await Place.next()


# состояние ввода 'адреса' вызванное после предыдущего состояния
@dp.message_handler(state=Place.address)
async def process_address(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['address'] = message.text
        data['message_id'].extend([message.message_id])  # сохраняем идентификатор сообщения

        # добавляем место в базу данных
        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('CREATE TABLE IF NOT EXISTS places (name text, address text, rating integer DEFAULT 0)')

            # проверка на существование места в базе
            await cursor.execute('SELECT name FROM places WHERE name = ?', (data['name'],))
            result = await cursor.fetchone()
            if result is not None:
                bot_message = await message.answer("Это место уже есть в базе!")
                data['message_id'].extend([bot_message.message_id])
            else:
                await cursor.execute('INSERT INTO places (name, address) VALUES (?, ?)', (data['name'], data['address']))
                await db.commit()
                bot_message = await message.answer("Место успешно добавлено!")
                data['message_id'].extend([bot_message.message_id])

    await state.finish()

    # удаление всех сообщений через 3 сек после добавления места (во избежание захламления)
    await asyncio.sleep(3)
    for msg_id in data['message_id']:
        await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)


#  хэндлер реагирующий на команды /place
@dp.message_handler(Command('place'))
async def show_places(message: types.Message):

    # Удаляем сообщение с командой от пользователя (во избежание захламления)
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # подключаемся к бд и выводим список мест через цикл for
    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places')
        rows = await cursor.fetchall()
        if not rows:
            await message.answer("База данных пуста!")
        else:
            places_list = ''  # строка для хранения всех мест (для дальнейшего удаления)
            for row in rows:
                places_list += f"Название: {row[0]}\n"\
                               f"Адрес: {row[1]}\n"\
                               f"Средний рейтинг: {row[2]:.1f}\n\n"
            sent_message = await message.answer(places_list)
            await asyncio.sleep(60)  # список мест будет удален через 60 сек (во избежание захламления)
            await bot.delete_message(chat_id=message.chat.id, message_id=sent_message.message_id)


# Проверка, является ли пользователь администратором
# или доверенным лицом (его id находится в списке разрешенных)
async def admin_check(message: types.Message):
    chat_member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
    return chat_member.status in ["creator", "administrator"] or message.from_user.id in config.tg_bot.admin_ids


# хэндлер реагирующий на команду /del (доступна только админу)
@dp.message_handler(Command('del'), state="*")
async def start_del_cmd_handler(message: types.Message) -> None:

    # сохраняем сообщения в список для дальнейшего удаления
    state = dp.current_state(user=message.from_user.id)
    async with state.proxy() as data:
        data['messages_to_delete'] = [message.message_id]

    # проверяем, является ли пользователь администратором или находится ли его идентификатор в списке разрешенных
    if not await admin_check(message):
        sent_message = await message.answer("Вы не являетесь администратором или не имеете разрешения!")
        data['messages_to_delete'].append(sent_message.message_id)
        return

    sent_message = await message.answer("Введите название места, которое нужно удалить:")
    async with state.proxy() as data:
        data['messages_to_delete'].append(sent_message.message_id)

    await Del.name.set()


# состояние после вызова команды /del
@dp.message_handler(state=Del.name)
async def process_del_name(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['messages_to_delete'].append(message.message_id)

    if not await admin_check(message):
        sent_message = await message.answer("Вы не являетесь администратором!")
        data['messages_to_delete'].append(sent_message.message_id)

        await asyncio.sleep(2)
        # удаляем сообщения
        async with state.proxy() as data:
            for msg_id in data['messages_to_delete']:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except exceptions.MessageCantBeDeleted:
                    continue
            return

    async with state.proxy() as data:
        data['name'] = message.text

        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('DELETE FROM places WHERE name = ?', (data['name'],))
            await db.commit()

    sent_message = await message.answer("Место успешно удалено!")
    async with state.proxy() as data:
        data['messages_to_delete'].append(sent_message.message_id)

    await asyncio.sleep(2)
    # удаляем сообщения после удаления места
    async with state.proxy() as data:
        for msg_id in data['messages_to_delete']:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except exceptions.MessageCantBeDeleted:
                continue

    await state.finish()


# хэндлер реагирующий на команду /rating
@dp.message_handler(Command('rating'))
async def start_rating_cmd_handler(message: types.Message):
    state = dp.current_state(user=message.from_user.id)
    async with state.proxy() as data:
        data['messages_to_delete'] = [message.message_id]

    sent_message = await message.answer("Введите название места, которому хотите поставить оценку:")
    async with state.proxy() as data:
        data['messages_to_delete'].append(sent_message.message_id)

    await Rating.name.set()


# состояние ввода места вызванное после команды /rating
@dp.message_handler(state=Rating.name)
async def process_rating_name(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['name'] = message.text.lower()
        data['messages_to_delete'].append(message.message_id)

    sent_message = await message.answer("Введите оценку от 1 до 10:")
    async with state.proxy() as data:
        data['messages_to_delete'].append(sent_message.message_id)

    await Rating.next()


# состояние ввода оценки вызванное предыдущим состоянием
@dp.message_handler(state=Rating.rating)
async def process_rating(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        data['messages_to_delete'].append(message.message_id)
        try:
            data['rating'] = int(message.text)
            if not 1 <= data['rating'] <= 10:
                raise ValueError()
        except ValueError:
            sent_message = await message.answer("Оценка должна быть числом от 1 до 10. Попробуйте ещё раз.")
            data['messages_to_delete'].append(sent_message.message_id)
            await asyncio.sleep(3)
            for msg_id in data['messages_to_delete']:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except exceptions.MessageCantBeDeleted:
                    continue
            await state.reset_state()
            return

        # обновляем рейтинг места в базе данных
        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('SELECT * FROM places WHERE name = ?', (data['name'],))
            place = await cursor.fetchone()
            if place is None:
                sent_message = await message.answer("Такого места не существует в базе данных. Попробуйте ещё раз.")
                data['messages_to_delete'].append(sent_message.message_id)
                await asyncio.sleep(2)
                for msg_id in data['messages_to_delete']:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                    except exceptions.MessageCantBeDeleted:
                        continue
                await state.reset_state()
                return

            await cursor.execute('CREATE TABLE IF NOT EXISTS ratings (name text, rating integer)')
            await cursor.execute('INSERT INTO ratings (name, rating) VALUES (?, ?)', (data['name'], data['rating']))
            await cursor.execute('SELECT AVG(rating) FROM ratings WHERE name = ?', (data['name'],))
            avg_rating = await cursor.fetchone()
            await cursor.execute('UPDATE places SET rating = ? WHERE name = ?', (avg_rating[0], data['name']))
            await db.commit()

    sent_message = await message.answer("Рейтинг успешно обновлен!")
    async with state.proxy() as data:
        data['messages_to_delete'].append(sent_message.message_id)

    await asyncio.sleep(2)  # Пауза 2 секунды что бы успеть прочитать

    async with state.proxy() as data:
        for msg_id in data['messages_to_delete']:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except exceptions.MessageCantBeDeleted:
                continue

    await state.finish()


@dp.message_handler(Command('random'))
async def random_place(message: types.Message):

    # Удаляем сообщение с командой от пользователя
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places')
        rows = await cursor.fetchall()
        if rows:
            random_row = random.choice(rows)
            await message.answer(f"Название: {random_row[0]}\n"
                                 f"Адрес: {random_row[1]}\n"
                                 f"Рейтинг: {random_row[2]}\n")
        else:
            await message.answer("В базе данных пока нет интересных мест.")


@dp.message_handler(Command('poll'))
async def poll_command(message: types.Message):
    print(message.chat.id)

    # Удаляем сообщение с командой от пользователя
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    await bot.send_poll(
        chat_id=message.chat.id,
        question="Выберите время и день недели:",
        options=["Суббота | 12:00", "Суббота | 13:00", "Суббота | 14:00", "Суббота | 15:00", "Суббота | 17:00",
                 "Воскресенье | 12:00", "Воскресенье | 13:00", "Воскресенье | 14:00", "Воскресенье | 15:00", "Воскресенье | 17:00"],
    )


async def send_poll():
    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places ORDER BY RANDOM() LIMIT 7')
        places = await cursor.fetchall()
        place_options = [f"Место: {place[0]} | Адрес: {place[1]} | Рейтинг: {place[2]}" for place in places]

    await bot.send_poll(
        chat_id=-857034880,
        question="Выберите время и день недели:",
        options=["Суббота | 12:00", "Суббота | 13:00", "Суббота | 14:00", "Суббота | 15:00", "Суббота | 17:00",
                 "Воскресенье | 12:00", "Воскресенье | 13:00", "Воскресенье | 14:00", "Воскресенье | 15:00", "Воскресенье | 17:00"],
        is_anonymous=False,
    )

    await bot.send_poll(
        chat_id=-857034880,
        question="Выберите место:",
        options=place_options,
        is_anonymous=False,
    )


poll_results = defaultdict(lambda: defaultdict(int))


@dp.poll_answer_handler()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    for option_id in poll_answer.option_ids:
        poll_results[poll_answer.poll_id][option_id] += 1


async def check_poll_results():
    for poll_id, results in poll_results.items():
        max_votes = max(count for count in results.values())
        winners = [option for option, count in results.items() if count == max_votes]

        await bot.send_message(-857034880, f"Варианты с наибольшим числом голосов: {winners}")

if __name__ == '__main__':
    scheduler = AsyncIOScheduler()
    trigger = CronTrigger(day_of_week='wed', hour=13, minute=9)
    scheduler.add_job(send_poll, trigger)
    trigger1 = CronTrigger(day_of_week='wed', hour=13, minute=10)
    scheduler.add_job(check_poll_results, trigger1)
    scheduler.start()
    executor.start_polling(dp, skip_updates=True)
