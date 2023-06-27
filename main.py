import asyncio
import json
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

from databases.database import create_db

from states.states import Del, Place, Rating


# Устанавливаем настройки логирования для отладки бота
logging.basicConfig(level=logging.INFO)

# Загрузка конфига с данными для бота
config: Config = load_config()

# Создаем экземпляры бота, хранилища и диспетчера
# передавая в качестве аргументов токен бота и хранилище состояний
bot = Bot(token=config.tg_bot.token)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Список разрешенных чатов для добавления места
allowed_chat = config.tg_bot.allowed_chat_ids
target_chat = config.tg_bot.target_chat_ids


@dp.message_handler(Command(commands=['start', 'help']))
async def help_command(message: types.Message) -> None:
    # Функция-обработчик команд '/start' и '/help'
    # Если пользователь отправляет одну из этих команд
    # бот отвечает соответствующим сообщением

    if 'start' in message.text:
        await bot.send_message(message.chat.id, "Привет, я бот органайзер!\nДоступные команды - /help")
        await create_db()  # создается база данных при запуске бота
    else:
        help_text = "✋ДОСТУПНЫЕ КОМАНДЫ!🤚\n\n" \
            "/add - Добавить новое место\n" \
            "/del - Удалить место (только для администраторов)\n" \
            "/place - Вывести список всех мест\n" \
            "/random - Выбрать случайное место\n" \
            "/rating - Поставить оценку выбранному месту\n"

        await message.answer(help_text)

    # Удаляем сообщение с командой от пользователя
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


@dp.message_handler(Command('add'))
async def start_cmd_handler(message: types.Message) -> None:
    # Функция-обработчик команды '/add'
    # Когда пользователь отправляет эту команду,
    # начинается диалог для добавления нового места

    # Проверка на принадлежность к определенному чату
    if message.chat.id not in allowed_chat:
        bot_message = await message.answer('🚫 Эта команда доступна только для чата: "IT Завтраки, Тбилиси" 🚫')
        await asyncio.sleep(5)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await bot.delete_message(chat_id=message.chat.id, message_id=bot_message.message_id)

        return

    state = dp.current_state(chat=message.chat.id, user=message.from_user.id)
    bot_message = await message.answer("Введите название места:👾")
    await state.update_data(message_id=[message.message_id, bot_message.message_id])  # сохраняем идентификаторы сообщений
    await Place.name.set()


@dp.message_handler(state=Place.name)
async def process_name(message: types.Message, state: FSMContext):
    # Этот обработчик активируется после команды '/add'
    # при условии что он прошел проверку на принадлежность к чату
    # и спрашивает у пользователя название места

    async with state.proxy() as data:
        data['name'] = message.text.lower()
        data['message_id'].extend([message.message_id])  # сохраняем идентификатор сообщения

        # Проверяем наличие места в базе данных
        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('SELECT name FROM places WHERE name = ?', (data['name'],))
            result = await cursor.fetchone()

            if result is not None:
                bot_message = await message.answer("❌ Это место уже есть в базе! ❌")
                data['message_id'].extend([bot_message.message_id])
                await state.finish()
                await asyncio.sleep(1)
                for msg_id in data['message_id']:
                    await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            else:
                bot_message = await message.answer("Введите адрес места:📍")
                data['message_id'].extend([bot_message.message_id])
                await Place.next()


@dp.message_handler(state=Place.address)
async def process_address(message: types.Message, state: FSMContext):
    # Этот обработчик активируется после ввода имени места и запрашивает адрес места
    # Он также проверяет, существует ли уже это место в базе данных и, если нет, добавляет его

    async with state.proxy() as data:
        data['address'] = message.text
        data['message_id'].extend([message.message_id])  # сохраняем идентификатор сообщения

        # Добавляем место в базу данных
        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('INSERT INTO places (name, address) VALUES (?, ?)', (data['name'], data['address']))
            await db.commit()
            bot_message = await message.answer("✅ Место успешно добавлено! ✅")
            data['message_id'].extend([bot_message.message_id])

    await state.finish()

    # удаление всех сообщений через 1 сек после добавления места (во избежание захламления)
    await asyncio.sleep(1)
    for msg_id in data['message_id']:
        await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)


@dp.message_handler(Command('place'))
async def show_places(message: types.Message):
    # Обработчик команды '/place', выводит список всех мест из базы данных.
    # Если база данных пуста, то будет отправлено соответствующее сообщение.

    # Удаляем сообщение с командой от пользователя (во избежание захламления)
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # Gодключаемся к бд и выводим список мест через цикл for
    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places')
        rows = await cursor.fetchall()
        if not rows:
            await message.answer("База данных пуста! 🤷🏽‍♂️")
        else:
            places_list = '👉СПИСОК ВСЕХ МЕСТ В БАЗЕ👈\n\n'  # Cтрока для хранения всех мест (для дальнейшего удаления)
            for row in rows:
                places_list += f"Место: {row[0]}\n"\
                               f"Адрес: {row[1]}\n"\
                               f"Рейтинг: {row[2]:.1f}\n\n"
            sent_message = await message.answer(places_list)
            await asyncio.sleep(60)  # список мест будет удален через 60 сек (во избежание захламления)
            await bot.delete_message(chat_id=message.chat.id, message_id=sent_message.message_id)


@dp.message_handler(Command('del'), state="*")
async def start_del_cmd_handler(message: types.Message) -> None:
    # Обработчик команды '/del', удаляет место из базы данных.
    # Если пользователь не является администратором и его ID не указан в конфигурационном файле,
    # то будет отправлено сообщение об ошибке.

    state = dp.current_state(user=message.from_user.id)

    async with state.proxy() as data:
        # Cохраняем сообщения в список для дальнейшего удаления
        data['messages_to_delete'] = [message.message_id]
        data['attempts'] = 3

        # Проверяем, является ли пользователь администратором или находится ли его идентификатор в списке разрешенных
        if not await admin_check(message):
            sent_message = await message.answer("Вы не являетесь администратором или не имеете разрешения! 🤬")
            data['messages_to_delete'].append(sent_message.message_id)

            # Добавляем задержку
            await asyncio.sleep(10)

            for msg_id in data['messages_to_delete']:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except exceptions.MessageCantBeDeleted:
                    continue
            return

        sent_message = await message.answer("Введите название места, которое нужно удалить:🥸")
        data['messages_to_delete'].append(sent_message.message_id)

    await Del.name.set()


@dp.message_handler(state=Del.name)
async def process_del_name(message: types.Message, state: FSMContext):
    # Этот обработчик отвечает на следующий этап после команды '/del'.
    # Он принимает название места, которое необходимо удалить.
    # Если место не найдено в базе данных, пользователю предоставляется еще несколько попыток для ввода.
    # Если место найдено, оно удаляется из базы данных.

    async with state.proxy() as data:
        data['messages_to_delete'].append(message.message_id)

        if not await admin_check(message):
            sent_message = await message.answer("Вы не являетесь администратором! 🤬")
            data['messages_to_delete'].append(sent_message.message_id)

            await asyncio.sleep(1)
            # Удаляем сообщения
            for msg_id in data['messages_to_delete']:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except exceptions.MessageCantBeDeleted:
                    continue
            return

        data['name'] = message.text

        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('SELECT * FROM places WHERE name = ?', (data['name'],))
            result = await cursor.fetchone()

            if result is None:
                data['attempts'] -= 1
                if data['attempts'] > 0:
                    sent_message = await message.answer(f"❌ Место '{data['name']}' не найдено. Попробуйте снова: \
                                                        попыток осталось {data['attempts']} ❌")
                    data['messages_to_delete'].append(sent_message.message_id)
                else:
                    sent_message = await message.answer("Превышено количество попыток. Операция отменена. 💥")
                    data['messages_to_delete'].append(sent_message.message_id)

                    # Удаляем сообщения
                    for msg_id in data['messages_to_delete']:
                        try:
                            await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                        except exceptions.MessageCantBeDeleted:
                            continue
                    data['attempt_counter'] = 3  # Сбрасываем счетчик попыток
                    await state.reset_state()  # Сбрасываем состояние
                return

            await cursor.execute('DELETE FROM places WHERE name = ?', (data['name'],))
            await db.commit()

            sent_message = await message.answer("✅ Место успешно удалено! ✅")
            data['messages_to_delete'].append(sent_message.message_id)

            await asyncio.sleep(1)

            # Удаляем сообщения после удаления места
            for msg_id in data['messages_to_delete']:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                except exceptions.MessageCantBeDeleted:
                    continue

            await state.finish()


@dp.message_handler(Command('rating'))
async def start_rating_cmd_handler(message: types.Message):
    # Обработчик команды /rating. Он устанавливает текущее состояние
    # для пользователя и начинает диалог, просив пользователя ввести
    # название места, которое он хочет оценить.

    # Проверка на принадлежность к определенному чату
    if message.chat.id not in allowed_chat:
        bot_message = await message.answer('🚫 Эта команда доступна только для чата: "IT Завтраки, Тбилиси" 🚫')
        await asyncio.sleep(5)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await bot.delete_message(chat_id=message.chat.id, message_id=bot_message.message_id)

        return

    state = dp.current_state(user=message.from_user.id)
    async with state.proxy() as data:
        data['messages_to_delete'] = [message.message_id]
        sent_message = await message.answer("Введите название места, которому хотите поставить оценку:🫶🏻")
        data['messages_to_delete'].append(sent_message.message_id)
    await Rating.name.set()


@dp.message_handler(state=Rating.name)
async def process_rating_name(message: types.Message, state: FSMContext):
    # Этот обработчик обрабатывает введенное пользователем название места.
    # Проверяет, существует ли это место в базе данных. Если место не найдено,
    # пользователю дается еще 2 попытки для повторного ввода.
    # После того, как место успешно найдено, пользователю предлагается ввести
    # оценку от 1 до 10.

    async with state.proxy() as data:
        data['name'] = message.text.lower()
        data['messages_to_delete'].append(message.message_id)

        if 'attempt_counter' not in data:
            data['attempt_counter'] = 2
        else:
            data['attempt_counter'] -= 1

        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('SELECT * FROM places WHERE name = ?', (data['name'],))
            place = await cursor.fetchone()
            if place is None:
                if data['attempt_counter'] > 0:
                    sent_message = await message.answer(f"❌ Такого места не существует в базе данных. \
                                                        Попробуйте ещё раз. Попыток осталось {data['attempt_counter']} ❌")
                    data['messages_to_delete'].append(sent_message.message_id)
                else:
                    sent_message = await message.answer("Вы исчерпали все попытки...🤦🏼‍♂️")
                    data['messages_to_delete'].append(sent_message.message_id)
                    await asyncio.sleep(1)
                    for msg_id in data['messages_to_delete']:
                        try:
                            await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                        except exceptions.MessageCantBeDeleted:
                            continue
                    data['attempt_counter'] = 3
                    await state.reset_state()
                return
            else:
                data['attempt_counter'] = 3
                sent_message = await message.answer("Введите оценку от 1 до 10: ✨")
                data['messages_to_delete'].append(sent_message.message_id)
        await Rating.next()


@dp.message_handler(state=Rating.rating)
async def process_rating(message: types.Message, state: FSMContext):
    # Этот обработчик обрабатывает введенную пользователем оценку.
    # Если оценка, не является целым числом от 1 до 10, пользователю дается еще 2 попытки
    # для ввода. После успешного ввода оценки, она сохраняется в базе данных.

    async with state.proxy() as data:
        data['messages_to_delete'].append(message.message_id)

        if 'attempt_counter' not in data:
            data['attempt_counter'] = 2
        else:
            data['attempt_counter'] -= 1

        try:
            data['rating'] = int(message.text)
            if not 1 <= data['rating'] <= 10:
                raise ValueError()
        except ValueError:
            if data['attempt_counter'] > 0:
                sent_message = await message.answer(f"❌ Оценка должна быть целым числом от 1 до 10.\
                                                    Попробуйте ещё раз. Попыток осталось: {data['attempt_counter']}❌")
                data['messages_to_delete'].append(sent_message.message_id)
            else:
                sent_message = await message.answer("Вы исчерпали все попытки...🤦🏼‍♂️")
                data['messages_to_delete'].append(sent_message.message_id)
                await asyncio.sleep(1)
                for msg_id in data['messages_to_delete']:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                    except exceptions.MessageCantBeDeleted:
                        continue
                data['attempt_counter'] = 3
                await state.reset_state()
            return

        async with aiosqlite.connect('places.db') as db:
            cursor = await db.cursor()
            await cursor.execute('SELECT * FROM places WHERE name = ?', (data['name'],))
            place = await cursor.fetchone()
            if place is None:
                sent_message = await message.answer("Такого места не существует в базе данных. Попробуйте ещё раз. 🤷🏽‍♂️")
                data['messages_to_delete'].append(sent_message.message_id)
                await asyncio.sleep(1)
                for msg_id in data['messages_to_delete']:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
                    except exceptions.MessageCantBeDeleted:
                        continue
                await state.reset_state()
                return

            await cursor.execute('INSERT INTO ratings (name, rating) VALUES (?, ?)', (data['name'], data['rating']))
            await cursor.execute('SELECT AVG(rating) FROM ratings WHERE name = ?', (data['name'],))
            avg_rating = await cursor.fetchone()
            await cursor.execute('UPDATE places SET rating = ? WHERE name = ?', (avg_rating[0], data['name']))
            await db.commit()

        sent_message = await message.answer("✅ Рейтинг успешно обновлен! ✅")
        data['messages_to_delete'].append(sent_message.message_id)
        await asyncio.sleep(1)
        for msg_id in data['messages_to_delete']:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except exceptions.MessageCantBeDeleted:
                continue

        await state.finish()


@dp.message_handler(Command('random'))
async def random_place(message: types.Message):
    # Обработчик реагирует на команду /random
    # Отправляет пользователю случайное место
    # из базы данных

    # Удаляем сообщение с командой от пользователя
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places')
        rows = await cursor.fetchall()
        if rows:
            random_row = random.choice(rows)
            answer = await message.answer("👉СЛУЧАЙНОЕ МЕСТО!👈\n\n"
                                          f"Название: {random_row[0]}\n"
                                          f"Адрес: {random_row[1]}\n"
                                          f"Рейтинг: {random_row[2]}\n")
            await asyncio.sleep(20)
            await bot.delete_message(chat_id=message.chat.id, message_id=answer.message_id)
        else:
            await message.answer("В базе данных пока нет интересных мест. 🤷🏽‍♂️")


@dp.poll_answer_handler()
async def handle_poll_answer(poll_answer: types.PollAnswer):
    # Ловит ответ на опрос и затем записывает его в базу данных SQLite.
    # Если такой ответ уже существует, то он просто увеличивает количество голосов.

    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()

        for option_id in poll_answer.option_ids:
            await cursor.execute('INSERT INTO poll_results (poll_id, option_id, votes) VALUES (?, ?, 1) '
                                 'ON CONFLICT(poll_id, option_id) DO UPDATE SET votes = votes + 1',
                                 (poll_answer.poll_id, option_id))

        await db.commit()


async def admin_check(message: types.Message):
    # Функция для проверки, является ли пользователь администратором
    # или его ID включен в список разрешенных ID из конфигурационного файла.

    chat_member = await bot.get_chat_member(chat_id=message.chat.id, user_id=message.from_user.id)
    return chat_member.status in ["creator", "administrator"] or message.from_user.id in config.tg_bot.admin_ids


async def send_poll():
    # Функция отвечает за отправку двух опросов в чат Telegram.
    # Один опрос связан с выбором времени и дня недели,
    # а другой опрос связан с выбором места из списка,
    # который изначально был получен из базы данных.
    # Результаты опросов сохраняются в базе данных.

    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()
        await cursor.execute('SELECT * FROM places ORDER BY RANDOM() LIMIT 7')
        places = await cursor.fetchall()
        place_options = [f"Место: {place[0]} | Рейтинг: {place[2]}" for place in places]

    poll_message1 = await bot.send_poll(
        chat_id=-1001646936147,
        question="Выберите время и день недели:⏰",
        options=["Суббота | 11:00", "Суббота | 12:00", "Суббота | 15:00", "Суббота | 16:00", "Суббота | 17:00",
                 "Воскресенье | 11:00", "Воскресенье | 12:00", "Воскресенье | 15:00", "Воскресенье | 16:00", "Воскресенье | 17:00"],
        is_anonymous=False,
        allows_multiple_answers=True,
    )

    poll_message2 = await bot.send_poll(
        chat_id=-1001646936147,
        question="Выберите место:🍔",
        options=[*place_options, 'Парк'],
        is_anonymous=False,
        allows_multiple_answers=True,
    )

    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()

        await cursor.execute('INSERT INTO poll_data (poll_id, options) VALUES (?, ?)',
                             (poll_message1.poll.id, json.dumps([option.text for option in poll_message1.poll.options])))

        await cursor.execute('INSERT INTO poll_data (poll_id, options) VALUES (?, ?)',
                             (poll_message2.poll.id, json.dumps([option.text for option in poll_message2.poll.options])))

        await db.commit()


async def check_poll_results():
    # Функция просматривает все опросы в базе данных,
    # выбирает победителя каждого опроса (то есть вариант с наибольшим числом голосов),
    # формирует текстовое сообщение с результатами и отправляет это сообщение в чат.
    # Затем все данные об опросах удаляются из базы данных.

    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()

        await cursor.execute('SELECT * FROM poll_data')
        all_polls = await cursor.fetchall()

        results_text = list()

        for poll in all_polls:
            poll_id, options = poll[0], json.loads(poll[1])

            await cursor.execute('SELECT option_id, MAX(votes) FROM poll_results WHERE poll_id = ?', (poll_id,))
            winner = await cursor.fetchone()

            if winner is not None:
                winners_text = options[winner[0]]
                results_text.append(winners_text)

        if len(results_text) >= 2:
            await bot.send_message(-1001646936147, f'♨️Уважемые причастные! Данные вашей встречи!♨️\n\n'
                                   f'Когда: {results_text[0]}\n{results_text[1]}')
        else:
            await bot.send_message(-1001646936147, 'Нет достаточного количества данных для вывода результатов.')

        # Очищаем данные опроса
        await cursor.execute('DELETE FROM poll_data')
        await cursor.execute('DELETE FROM poll_results')

        await db.commit()

if __name__ == '__main__':
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_poll, CronTrigger(day_of_week='mon', hour=13, minute=32))
    scheduler.add_job(check_poll_results, CronTrigger(day_of_week='fri', hour=13))
    scheduler.start()
    executor.start_polling(dp, skip_updates=True)
