
import asyncio
import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def main():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        print("ERROR: DATABASE_URL not found")
        return

    try:
        connection = await asyncpg.connect(
            database_url,
            ssl="require"
        )

        version = await connection.fetchval("SELECT version()")

        print("SUPABASE CONNECTION: SUCCESS")
        print(version.splitlines()[0])

        await connection.close()

    except Exception as error:
        print("CONNECTION ERROR:")
        print(type(error).__name__, str(error))

asyncio.run(main())
