import aiosqlite


async def create_db():
    async with aiosqlite.connect('places.db') as db:
        cursor = await db.cursor()

        await cursor.executescript('''
            CREATE TABLE IF NOT EXISTS poll_data (
                poll_id TEXT PRIMARY KEY,
                options TEXT
            );

            CREATE TABLE IF NOT EXISTS poll_results (
                poll_id TEXT,
                option_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS places (
                name text,
                address text,
                rating integer DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ratings (
                name text,
                rating integer
            );
        ''')

        await db.commit()
