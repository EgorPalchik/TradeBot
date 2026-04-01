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
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx

# ════════════════════════════════════════════════════════════
#  ⚙️  CONFIG - НАСТРОЙ ПОД СЕБЯ
# ════════════════════════════════════════════════════════════

AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzQ3MDQ1MzksImlkIjoiNzc0OTEifQ.cMS2LNi0fG3bZv5KGZStHbmP6ntAnu6nKg4n4LWh0LE"
MY_BALANCE_TON = 10
PROXY = None

# Режим цены:
# "fixed" - фиксированный лимит MAX_PRICE_TON
# "floating" - плавающий лимит от флора (процент от флора)
PRICE_MODE = "floating"  # "fixed" или "floating"

# Фиксированный лимит (используется если PRICE_MODE = "fixed")
FIXED_PRICE_TON = 3.41

# Плавающий лимит: процент от флора (например, -5% означает покупать на 5% дешевле флора)
# Можно положительное или отрицательное число
FLOATING_PERCENT = -2.29  # -5% от флора

# Чёрный список NFT
# Поддерживаются форматы:
#   "chillflame-17546299"  — slug из URL
#   "Chill Flame"          — название коллекции
#   "Tiki Torch"           — название модели
#   "59277"                — giftNumber
BLACKLIST: set = {
     "chillflame-17546299",  # пример
    # "Tiki Torch",           # пример
}

POLL_INTERVAL = 0.5  # интервал между сканированиями (сек)
FETCH_LIMIT = 100  # количество NFT за запрос
DRY_RUN = False  # True - только логи, False - реальные покупки
MAX_ATTEMPTS = 5  # попыток для одного NFT

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

QUERY_ESTIMATE = """
query GetExchangeInventoryEstimation($input: ExchangeInventoryEstimateInput!) {
  exchangeInventoryEstimate(input: $input) {
    success
    code
    message
    inventoryItemsPriceTotal
    assetsPriceTotal
    differenceTonAmount
    requiredTopUpTonAmount
    requiredTopUpStarsAmount
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
        "X-Client-Version": "530af74d",
    }


# ════════════════════════════════════════════════════════════
#  Логирование
# ════════════════════════════════════════════════════════════

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log_ping(n: int, count: int, ms: int, floor_p: Optional[float], frozen: int, balance: float, current_limit: float):
    floor_str = f"{floor_p} TON" if floor_p is not None else "—"
    print(
        f"[{ts()}] 🔄 PING #{n:<5} | лотов: {count:<3} | флор: {floor_str:<10} | лимит: {current_limit:.4f} | баланс: {balance:.4f} | зам: {frozen} | {ms}ms")


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
    # Не выводим blacklist пропуски, чтобы не спамить
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


def log_price_mode(mode: str, fixed_price: float, floating_percent: int):
    if mode == "fixed":
        print(f"[{ts()}] 📊 РЕЖИМ ЦЕНЫ: фиксированный | Лимит: {fixed_price} TON")
    else:
        print(f"[{ts()}] 📊 РЕЖИМ ЦЕНЫ: плавающий | Процент от флора: {floating_percent}%")


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
        retries: int = 2,
) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            payload = {"query": query.strip(), "variables": variables}
            resp = await client.post(GQL_URL, json=payload)

            if resp.status_code != 200:
                if resp.status_code == 422 and attempt < retries:
                    await asyncio.sleep(0.5)
                    continue
                elif resp.status_code != 200:
                    if attempt < retries:
                        await asyncio.sleep(0.5)
                        continue
                return None

            data = resp.json()
            if "errors" in data:
                if attempt < retries:
                    await asyncio.sleep(0.5)
                    continue
                return None

            return data.get("data")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            if attempt < retries:
                await asyncio.sleep(0.5)
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


async def estimate_purchase(client: httpx.AsyncClient, asset_id: int) -> dict:
    data = await gql(client, QUERY_ESTIMATE, {
        "input": {"userInventoryItemIds": [], "assetIds": [asset_id]}
    })
    if not data:
        return {}
    return data.get("exchangeInventoryEstimate", {})


async def buy_asset(
        client: httpx.AsyncClient,
        asset_id: int,
        assets_price_total: float,
        inventory_items_price_total: float = 0
) -> tuple[bool, str]:
    variables = {
        "userInventoryItemIds": [],
        "assetIds": [asset_id],
        "useTonFromBalance": assets_price_total,
        "assetsPriceTotal": assets_price_total,
        "inventoryItemsPriceTotal": inventory_items_price_total,
    }

    data = await gql(client, MUTATION_BUY, variables)
    if not data:
        return False, "NO_RESPONSE"

    result = data.get("exchangeInventoryItemsForAssets", {})
    success = result.get("success", False)
    code = result.get("code", "")
    msg = result.get("message", "")

    return success, f"{code} {msg}".strip()


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
    prices = [
        a["exchangePriceTon"]
        for a in items
        if a["id"] not in skip_ids
           and not is_blacklisted(a)
           and a.get("exchangePriceTon") is not None
    ]
    return min(prices) if prices else None


def calculate_price_limit(floor_price: Optional[float]) -> float:
    """
    Рассчитывает текущий лимит цены в зависимости от режима
    """
    if PRICE_MODE == "fixed":
        return FIXED_PRICE_TON
    else:  # floating mode
        if floor_price is None:
            # Если флор не определен, используем фиксированный лимит как запасной
            return FIXED_PRICE_TON
        # Рассчитываем лимит как процент от флора
        # Например, FLOATING_PERCENT = -5 означает 95% от флора
        # FLOATING_PERCENT = 5 означает 105% от флора
        multiplier = 1 + (FLOATING_PERCENT / 100)
        limit = floor_price * multiplier
        return max(0.01, limit)  # Минимум 0.01 TON


# ════════════════════════════════════════════════════════════
#  Главный цикл
# ════════════════════════════════════════════════════════════

async def main():
    print("╔══════════════════════════════════════════════╗")
    print("║       SwapGift NFT Sniper Bot v1.9           ║")

    # Выводим информацию о режиме цены
    if PRICE_MODE == "fixed":
        print(f"║  Режим цены : фиксированный                     ║")
        print(f"║  Лимит      : {FIXED_PRICE_TON} TON                        ║")
    else:
        print(f"║  Режим цены : плавающий                        ║")
        print(f"║  Процент    : {FLOATING_PERCENT:+}% от флора                 ║")

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
    current_price_limit = FIXED_PRICE_TON  # начальное значение

    log_price_mode(PRICE_MODE, FIXED_PRICE_TON, FLOATING_PERCENT)

    proxies = {"http://": PROXY, "https://": PROXY} if PROXY else None

    async with httpx.AsyncClient(
            headers=get_headers(),
            timeout=httpx.Timeout(20.0, connect=10.0, read=20.0),
            follow_redirects=True,
            http2=False,
            proxies=proxies,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
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

                if ping_count % 20 == 0:
                    new_bal = await fetch_balance(client)
                    if new_bal is not None:
                        current_balance = new_bal
                        log_balance(current_balance)

                items = await fetch_market(client)
                elapsed_ms = int((time.monotonic() - t0) * 1000)

                all_skip = bought_ids | failed_ids
                real_floor = calc_floor(items, all_skip)

                # Рассчитываем текущий лимит цены на основе флора
                current_price_limit = calculate_price_limit(real_floor)

                log_ping(ping_count, len(items), elapsed_ms, real_floor, len(failed_ids), current_balance,
                         current_price_limit)

                if not items:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                for asset in items:
                    aid = asset["id"]

                    # Пропускаем без логирования
                    if aid in bought_ids or aid in failed_ids:
                        continue

                    price = asset.get("exchangePriceTon", 9999)

                    # Используем динамический лимит
                    if price > current_price_limit or price > current_balance:
                        break

                    # Проверка blacklist - пропускаем без вывода
                    if is_blacklisted(asset):
                        continue

                    log_found(asset)

                    if DRY_RUN:
                        bought_ids.add(aid)
                        continue

                    # Пытаемся купить сразу, без estimate для увеличения скорости
                    price_to_pay = price
                    inventory_total = 0

                    success, buy_code = await buy_asset(client, aid, price_to_pay, inventory_total)

                    if success:
                        log_buy_ok(asset)
                        bought_ids.add(aid)
                        attempt_counts.pop(aid, None)
                        current_balance -= price_to_pay
                        total_spent += price_to_pay
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

                spent = time.monotonic() - t0
                await asyncio.sleep(max(0.0, POLL_INTERVAL - spent))

        except KeyboardInterrupt:
            print(f"\n[{ts()}] 🛑 Бот остановлен")
            print(
                f"[{ts()}] 📊 Финальная статистика | Куплено: {len(bought_ids)} NFT | Потрачено: {total_spent:.4f} TON")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{ts()}] 🛑 Бот остановлен")