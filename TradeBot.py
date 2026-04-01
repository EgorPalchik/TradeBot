#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║       SwapGift NFT Sniper Bot v1.9           ║
║  Авто-покупка NFT на swapgift.live           ║
╚══════════════════════════════════════════════╝
"""

import asyncio
import json
import time
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx

# ════════════════════════════════════════════════════════════
#  ⚙️  CONFIG - НАСТРОЙ ПОД СЕБЯ
# ════════════════════════════════════════════════════════════

# Токен авторизации (из переменных окружения)
AUTH_TOKEN = os.environ.get('AUTH_TOKEN', "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzUxMDI2MzEsImlkIjoiNzc0OTEifQ.ycBjJd9_jxKl7IVysacU8deNDg8aDVDmx9FWYzCZtWo")
CLIENT_VERSION = "46178330"
MY_BALANCE_TON = float(os.environ.get('MY_BALANCE_TON', '100'))
PROXY = None
MAX_PRICE_TON = 3

BLACKLIST: set = {
    "chillflame-17546299",
}

POLL_INTERVAL = 0.5
FETCH_LIMIT = 50
DRY_RUN = False
MAX_ATTEMPTS = 3

# ════════════════════════════════════════════════════════════
#  GraphQL
# ════════════════════════════════════════════════════════════

GQL_URL = "https://swapgift.live/api/graphql/query"

MUTATION_BUY = """
mutation ExchangeInventoryItemsForAssets($userInventoryItemIds: [ID!]!, $assetIds: [ID!]!, $useTonFromBalance: Float, $assetsPriceTotal: Float!, $inventoryItemsPriceTotal: Float!) {
  exchangeInventoryItemsForAssets(
    userInventoryItemIds: $userInventoryItemIds
    assetIds: $assetIds
    useTonFromBalance: $useTonFromBalance
    assetsPriceTotal: $assetsPriceTotal
    inventoryItemsPriceTotal: $inventoryItemsPriceTotal
  ) {
    success
    code
    message
    differenceTonAmount
    requiredTopUpTonAmount
    __typename
  }
}
"""

QUERY_GET_ASSETS = """
query GetAssets($cursor: String, $limit: Int64!, $filter: AssetsFilter, $sort: [AssetsSort!]) {
  assets(cursor: $cursor, limit: $limit, filter: $filter, sort: $sort) {
    items {
      id
      name
      exchangePriceTon
      url
      giftNumber
      modelName
    }
    total
    nextCursor
  }
}
"""

QUERY_BALANCE = """
query GetProfile {
  myProfile {
    balance
  }
}
"""

ASSET_FILTER = {"types": ["TELEGRAM_GIFT"], "currencies": ["TON"]}
ASSET_SORT = [{"field": "PRICE", "direction": "ASC"}]


# ════════════════════════════════════════════════════════════
#  Заголовки
# ════════════════════════════════════════════════════════════

def get_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://swapgift.live",
        "Referer": "https://swapgift.live/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Client-Version": CLIENT_VERSION,
    }


# ════════════════════════════════════════════════════════════
#  Логирование
# ════════════════════════════════════════════════════════════

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def log_ping(n: int, count: int, ms: int, floor_p: Optional[float], frozen: int, balance: float):
    floor_str = f"{floor_p} TON" if floor_p is not None else "—"
    print(f"[{ts()}] 🔄 PING #{n:<5} | лотов: {count:<3} | флор: {floor_str:<10} | лимит: {MAX_PRICE_TON:.4f} | баланс: {balance:.4f} | зам: {frozen} | {ms}ms")

def log_found(asset: dict):
    print(f"[{ts()}] ✅ НАЙДЕНО  | {asset['name']} | {asset['exchangePriceTon']} TON | id={asset['id']}")

def log_buy_ok(asset: dict):
    print(f"[{ts()}] 💰 КУПЛЕНО  | {asset['name']} | {asset['exchangePriceTon']} TON | id={asset['id']} ✓")

def log_buy_fail(asset: dict, reason: str = "", attempt: int = None):
    attempt_str = f" (попытка {attempt}/{MAX_ATTEMPTS})" if attempt is not None else ""
    print(f"[{ts()}] ❌ ОШИБКА   | id={asset['id']}{attempt_str} — {reason}")

def log_skip(asset: dict, reason: str):
    if reason != "blacklist":
        print(f"[{ts()}] ⏭️  ПРОПУСК | {asset.get('name', '?')} — {reason}")

def log_frozen(asset: dict):
    print(f"[{ts()}] 🧊 ЗАМОРОЖЕН| id={asset['id']} — {MAX_ATTEMPTS} неудач")

def log_err(msg: str):
    print(f"[{ts()}] 🔴 ERR     | {msg}")

def log_balance(balance: float):
    print(f"[{ts()}] 💰 Баланс  : {balance:.4f} TON")

def log_stats(total_bought: int, total_spent: float):
    print(f"[{ts()}] 📊 СТАТИСТИКА | Куплено: {total_bought} NFT | Потрачено: {total_spent:.4f} TON")


# ════════════════════════════════════════════════════════════
#  Чёрный список
# ════════════════════════════════════════════════════════════

def is_blacklisted(asset: dict) -> bool:
    url = asset.get("url", "")
    if url:
        slug = url.rstrip("/").split("/")[-1]
        if slug in BLACKLIST:
            return True
    return False


# ════════════════════════════════════════════════════════════
#  API
# ════════════════════════════════════════════════════════════

async def gql(client: httpx.AsyncClient, query: str, variables: dict) -> Optional[dict]:
    try:
        payload = {"query": query.strip(), "variables": variables}
        resp = await client.post(GQL_URL, json=payload, timeout=10.0)
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        if "errors" in data:
            return None
        
        return data.get("data")
    except Exception:
        return None


async def fetch_balance(client: httpx.AsyncClient) -> Optional[float]:
    data = await gql(client, QUERY_BALANCE, {})
    if not data:
        return None
    balance = data.get("myProfile", {}).get("balance")
    return float(balance) if balance is not None else None


async def fetch_market(client: httpx.AsyncClient) -> List[dict]:
    data = await gql(client, QUERY_GET_ASSETS, {
        "limit": FETCH_LIMIT,
        "filter": ASSET_FILTER,
        "sort": ASSET_SORT,
        "cursor": None,
    })
    if not data:
        return []
    items = data.get("assets", {}).get("items", [])
    return items if isinstance(items, list) else []


async def buy_asset(client: httpx.AsyncClient, asset_id: int, price: float) -> tuple[bool, str]:
    variables = {
        "userInventoryItemIds": [],
        "assetIds": [asset_id],
        "useTonFromBalance": price,
        "assetsPriceTotal": price,
        "inventoryItemsPriceTotal": 0,
    }
    
    data = await gql(client, MUTATION_BUY, variables)
    if not data:
        return False, "NO_RESPONSE"
    
    result = data.get("exchangeInventoryItemsForAssets", {})
    success = result.get("success", False)
    code = result.get("code", "")
    return success, code


# ════════════════════════════════════════════════════════════
#  Вспомогательные
# ════════════════════════════════════════════════════════════

def is_fatal_code(code: str) -> bool:
    fatal_codes = {"INVALID_ASSET_IDS", "ASSET_NOT_FOUND", "ALREADY_SOLD", "NOT_FOUND", "INSUFFICIENT_BALANCE"}
    return any(fc in code for fc in fatal_codes)


def calc_floor(items: List[dict], skip_ids: set) -> Optional[float]:
    prices = [a["exchangePriceTon"] for a in items if a["id"] not in skip_ids and not is_blacklisted(a)]
    return min(prices) if prices else None


# ════════════════════════════════════════════════════════════
#  Главная функция (запускается один раз)
# ════════════════════════════════════════════════════════════

async def main():
    print("╔══════════════════════════════════════════════╗")
    print("║       SwapGift NFT Sniper Bot v1.9           ║")
    print(f"║  Лимит цены : {MAX_PRICE_TON} TON                        ║")
    print(f"║  DRY RUN    : {'ДА' if DRY_RUN else 'НЕТ'}                              ║")
    print("╚══════════════════════════════════════════════╝\n")

    bought_ids: set = set()
    failed_ids: set = set()
    attempt_counts: Dict[int, int] = {}
    current_balance = MY_BALANCE_TON
    total_spent = 0.0

    async with httpx.AsyncClient(
        headers=get_headers(),
        timeout=10.0,
        follow_redirects=True,
    ) as client:

        real_balance = await fetch_balance(client)
        if real_balance is not None:
            current_balance = real_balance
            log_balance(current_balance)
        else:
            log_err("Не удалось получить баланс, используем значение из конфига")

        # Делаем один цикл сканирования и покупки
        items = await fetch_market(client)
        
        if not items:
            print("[{ts()}] Нет NFT для сканирования")
            return

        all_skip = bought_ids | failed_ids
        real_floor = calc_floor(items, all_skip)

        print(f"[{ts()}] Найдено {len(items)} NFT, флор: {real_floor} TON")

        for asset in items:
            aid = asset["id"]

            if aid in bought_ids or aid in failed_ids:
                continue

            price = asset.get("exchangePriceTon", 9999)

            if price > MAX_PRICE_TON or price > current_balance:
                continue

            if is_blacklisted(asset):
                continue

            log_found(asset)

            if DRY_RUN:
                bought_ids.add(aid)
                continue

            success, buy_code = await buy_asset(client, aid, price)

            if success:
                log_buy_ok(asset)
                bought_ids.add(aid)
                current_balance -= price
                total_spent += price
                log_balance(current_balance)
            else:
                log_buy_fail(asset, buy_code)

    print(f"\n[{ts()}] 📊 Статистика | Куплено: {len(bought_ids)} NFT | Потрачено: {total_spent:.4f} TON")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{ts()}] 🛑 Бот остановлен")
