import asyncio
import sys
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

# Добавляем корневую директорию в путь
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импортируем вашего бота
from TradeBot import main

async def handler(request):
    """Vercel Serverless функция"""
    
    # Добавляем переменные окружения
    os.environ['AUTH_TOKEN'] = os.environ.get('AUTH_TOKEN', '')
    os.environ['MY_BALANCE_TON'] = os.environ.get('MY_BALANCE_TON', '100')
    
    try:
        # Запускаем бота
        await main()
        
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json"
            },
            "body": '{"status": "Bot started successfully"}'
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json"
            },
            "body": f'{{"error": "{str(e)}"}}'
        }
