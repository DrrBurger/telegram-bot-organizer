from aiogram.dispatcher.filters.state import State, StatesGroup


class Place(StatesGroup):
    name = State()
    address = State()


class Del(StatesGroup):
    name = State()


class Rating(StatesGroup):
    name = State()
    rating = State()
