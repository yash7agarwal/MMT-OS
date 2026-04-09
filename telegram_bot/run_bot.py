"""
telegram_bot/run_bot.py — Entry point for the MMT-OS Telegram bot.

Usage:
    python -m telegram_bot.run_bot
    # or
    python telegram_bot/run_bot.py
"""

if __name__ == "__main__":
    from telegram_bot.bot import main

    main()
