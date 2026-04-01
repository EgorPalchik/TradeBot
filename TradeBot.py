#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║       SwapGift NFT Sniper Bot v1.9           ║
║  Авто-покупка NFT на swapgift.live           ║
╚══════════════════════════════════════════════╝

Зависимости:
    pip install httpx

Запуск:
    python swapgift_sniper.py
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

# Токен авторизации (из браузера)
AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzUxMDI2MzEsImlkIjoiNzc0OTEifQ.ycBjJd9_jxKl7IVysacU8deNDg8aDVDmx9FWYzCZtWo"
# Версия клиента (из заголовков)
CLIENT_VERSION = "46178330"

# Твой баланс TON (если не получается получить через API)
MY_BALANCE_TON = 100

# Фиксированный лимит покупки в TON
MAX_PRICE_TON = 3

# Чёрный список NFT
BLACKLIST: set = {
    "chillflame-17546299",
}

# Интервал между сканированиями (сек) - быстрый режим
POLL_INTERVAL = 0.05

# Количество NFT за один запрос
FETCH_LIMIT = 2

# True - только логи, False - реальные покупки
DRY_RUN = False

# Максимальное количество неудачных попыток для одного NFT
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
    updatedOurAssets {
      assetId
      oldPrice
      newPrice
      __typename
    }
    updatedTheirAssets {
      assetId
      oldPrice
      newPrice
      __typename
    }
    __typename
  }
}
"""

QUERY_GET_ASSETS = """
query GetAssets($cursor: String, $limit: Int64!, $filter: AssetsFilter, $sort: [AssetsSort!]) {
  assets(cursor: $cursor, limit: $limit, filter: $filter, sort: $sort) {
    items {
      id
      type
      currency
      exchangePriceTon
      exchangePriceUsdt
      purchasePrice
      url
      source
      name
      giftNumber
      photoUrl
      modelName
      modelRarityPermille
      backgroundName
      backgroundRarityPermille
      symbolName
      symbolRarityPermille
      isPlatform
      __typename
    }
    total
    limit
    nextCursor
    __typename
  }
}
"""

QUERY_BALANCE = """
query GetProfile {
  myProfile {
    balance
    __typename
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
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://swapgift.live",
        "Referer": "https://swapgift.live/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "X-Client-Version": CLIENT_VERSION,
    }


# ════════════════════════════════════════════════════════════
#  Логирование
# ════════════════════════════════════════════════════════════

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log_ping(n: int, count: int, ms: int, floor_p: Optional[float], frozen: int, balance: float):
    floor_str = f"{floor_p} TON" if floor_p is not None else "—"
    print(
        f"[{ts()}] 🔄 PING #{n:<5} | лотов: {count:<3} | флор: {floor_str:<10} | лимит: {MAX_PRICE_TON:.4f} | баланс: {balance:.4f} | зам: {frozen} | {ms}ms")


def log_found(asset: dict):
    print(
        f"[{ts()}] ✅ НАЙДЕНО  | "
        f"{asset['name']} [{asset.get('modelName', '?')}] | "
        f"#{asset.get('giftNumber', '?')} | "
        f"{asset['exchangePriceTon']} TON | id={asset['id']}"
    )


def log_buy_ok(asset: dict):
    print(
        f"[{ts()}] 💰 КУПЛЕНО  | "
        f"{asset['name']} [{asset.get('modelName', '?')}] | "
        f"{asset['exchangePriceTon']} TON | id={asset['id']} ✓"
    )


def log_buy_fail(asset: dict, reason: str = "", attempt: int = None):
    attempt_str = f" (попытка {attempt}/{MAX_ATTEMPTS})" if attempt is not None else ""
    print(f"[{ts()}] ❌ ОШИБКА   | id={asset['id']}{attempt_str} — {reason}")


def log_skip(asset: dict, reason: str):
    if reason != "blacklist":
        print(
            f"[{ts()}] ⏭️  ПРОПУСК | "
            f"{asset.get('name', '?')} [{asset.get('modelName', '?')}] — {reason}"
        )


def log_frozen(asset: dict):
    print(
        f"[{ts()}] 🧊 ЗАМОРОЖЕН| "
        f"{asset.get('name', '?')} [{asset.get('modelName', '?')}] "
        f"id={asset['id']} — {MAX_ATTEMPTS} неудач"
    )


def log_err(msg: str):
    print(f"[{ts()}] 🔴 ERR     | {msg}")


def log_balance(balance: float):
    print(f"[{ts()}] 💰 Баланс  : {balance:.4f} TON")


def log_stats(total_bought: int, total_spent: float):
    print(f"[{ts()}] 📊 СТАТИСТИКА | Куплено: {total_bought} NFT | Потрачено: {total_spent:.4f} TON")


# ════════════════════════════════════════════════════════════
#  Чёрный список
# ════════════════════════════════════════════════════════════

def _asset_keys(asset: dict) -> set:
    keys = set()
    url = asset.get("url", "")
    if url:
        slug = url.rstrip("/").split("/")[-1]
        if slug:
            keys.add(slug)
    gift_num = asset.get("giftNumber", "")
    if gift_num:
        keys.add(str(gift_num))
    name = asset.get("name", "")
    if name:
        keys.add(name)
    model = asset.get("modelName", "")
    if model:
        keys.add(model)
    return keys


def is_blacklisted(asset: dict) -> bool:
    return bool(_asset_keys(asset) & BLACKLIST)


# ════════════════════════════════════════════════════════════
#  API
# ════════════════════════════════════════════════════════════

async def gql(
        client: httpx.AsyncClient,
        query: str,
        variables: dict,
        retries: int = 1,
) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            payload = {"query": query.strip(), "variables": variables}
            resp = await client.post(GQL_URL, json=payload)

            if resp.status_code != 200:
                if attempt < retries:
                    continue
                return None

            data = resp.json()
            if "errors" in data:
                if attempt < retries:
                    continue
                return None

            return data.get("data")

        except:
            if attempt < retries:
                continue
            return None
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


async def buy_asset(
        client: httpx.AsyncClient,
        asset_id: int,
        assets_price_total: float,
) -> tuple[bool, str]:
    variables = {
        "userInventoryItemIds": [],
        "assetIds": [asset_id],
        "useTonFromBalance": assets_price_total,
        "assetsPriceTotal": assets_price_total,
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
    fatal_codes = {
        "INVALID_ASSET_IDS",
        "ASSET_NOT_FOUND",
        "ALREADY_SOLD",
        "NOT_FOUND",
        "INSUFFICIENT_BALANCE",
    }
    return any(fc in code for fc in fatal_codes)


def calc_floor(items: List[dict], skip_ids: set) -> Optional[float]:
    prices = []
    for a in items:
        if a["id"] not in skip_ids and not is_blacklisted(a) and a.get("exchangePriceTon") is not None:
            prices.append(a["exchangePriceTon"])
    return min(prices) if prices else None


# ════════════════════════════════════════════════════════════
#  Главный цикл
# ════════════════════════════════════════════════════════════

async def main():
    print("╔══════════════════════════════════════════════╗")
    print("║       SwapGift NFT Sniper Bot v1.9           ║")
    print(f"║  Лимит цены : {MAX_PRICE_TON} TON                        ║")
    print(f"║  Интервал   : {POLL_INTERVAL}s                           ║")
    print(f"║  DRY RUN    : {'ДА ⚠️  (тест)' if DRY_RUN else 'НЕТ 🔥 (реальные покупки)'}          ║")
    print(f"║  Max попыток: {MAX_ATTEMPTS}                              ║")
    print(f"║  Blacklist  : {len(BLACKLIST)} записей                      ║")
    print("╚══════════════════════════════════════════════╝\n")

    if AUTH_TOKEN == "ВСТАВЬ_СВОЙ_JWT_ТОКЕН_СЮДА":
        log_err("AUTH_TOKEN не задан!")
        return

    bought_ids: set = set()
    failed_ids: set = set()
    attempt_counts: Dict[int, int] = {}
    ping_count = 0
    current_balance = MY_BALANCE_TON
    total_spent = 0.0

    async with httpx.AsyncClient(
            headers=get_headers(),
            timeout=httpx.Timeout(2.0, connect=1.0),
            follow_redirects=True,
            http2=False,
    ) as client:

        real_balance = await fetch_balance(client)
        if real_balance is not None:
            current_balance = real_balance
            log_balance(current_balance)
        else:
            log_err("Не удалось получить баланс, используем значение из конфига")

        try:
            while True:
                t0 = time.monotonic()
                ping_count += 1

                items = await fetch_market(client)
                elapsed_ms = int((time.monotonic() - t0) * 1000)

                all_skip = bought_ids | failed_ids
                real_floor = calc_floor(items, all_skip)

                log_ping(ping_count, len(items), elapsed_ms, real_floor, len(failed_ids), current_balance)

                if not items:
                    continue

                for asset in items:
                    aid = asset["id"]

                    if aid in bought_ids or aid in failed_ids:
                        continue

                    price = asset.get("exchangePriceTon", 9999)

                    if price > MAX_PRICE_TON or price > current_balance:
                        break

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
                        attempt_counts.pop(aid, None)
                        current_balance -= price
                        total_spent += price
                        log_balance(current_balance)
                        log_stats(len(bought_ids), total_spent)
                    else:
                        if is_fatal_code(buy_code):
                            log_buy_fail(asset, f"fatal [{buy_code}]")
                            failed_ids.add(aid)
                        else:
                            attempt_counts[aid] = attempt_counts.get(aid, 0) + 1
                            cnt = attempt_counts[aid]
                            log_buy_fail(asset, f"code={buy_code}", attempt=cnt)
                            if cnt >= MAX_ATTEMPTS:
                                failed_ids.add(aid)
                                log_frozen(asset)


        except KeyboardInterrupt:
            print(f"\n[{ts()}] 🛑 Бот остановлен")
            print(f"[{ts()}] 📊 Финальная статистика | Куплено: {len(bought_ids)} NFT | Потрачено: {total_spent:.4f} TON")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{ts()}] 🛑 Бот остановлен")
