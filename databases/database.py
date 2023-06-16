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
                option_id INTEGER,
                votes INTEGER,
                PRIMARY KEY (poll_id, option_id)
            );

            CREATE TABLE IF NOT EXISTS places (
                name TEXT,
                address TEXT,
                rating INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS ratings (
                name TEXT,
                rating INTEGER
            );
        ''')

        await db.commit()
