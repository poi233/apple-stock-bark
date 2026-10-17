#!/usr/bin/env python3
"""Poll US Apple Store pickup availability and optionally send Bark alerts."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from scrapling.fetchers import FetcherSession

logging.getLogger("scrapling").setLevel(logging.WARNING)

BASE = "https://www.apple.com"
TIMEOUT = 15
MIN_DELAY = 60
MAX_BACKOFF = 900


class AppleError(Exception):
    def __init__(self, message: str, status: int | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class BarkError(Exception):
    pass


def validate_config(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    if value.get("storefront") != "us":
        raise ValueError("only storefront 'us' is supported (Apple English endpoint)")
    if not isinstance(value.get("postal_code"), str) or not value["postal_code"].strip():
        raise ValueError("postal_code must be a non-empty string")
    stores = value.get("stores")
    if not isinstance(stores, dict) or not stores:
        raise ValueError("stores must be a non-empty object")
    for store_id, name in stores.items():
        if not isinstance(store_id, str) or not re.fullmatch(r"R\d+", store_id):
            raise ValueError("store IDs must match R followed by digits")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("store names must be non-empty strings")
    products = value.get("products")
    if not isinstance(products, list) or not products:
        raise ValueError("products must be a non-empty array")
    parts = set()
    for product in products:
        if not isinstance(product, dict):
            raise ValueError("each product must be an object")
        name, part, raw_url = product.get("name"), product.get("part"), product.get("url")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("product name must be a non-empty string")
        if not isinstance(part, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,31}/[A-Za-z0-9]{1,8}", part):
            raise ValueError("product part must be an Apple SKU such as MW5G3AM/A")
        if part in parts:
            raise ValueError(f"duplicate product part: {part}")
        parts.add(part)
        if not isinstance(raw_url, str):
            raise ValueError("product url must be an Apple HTTPS product URL")
        parsed = urllib.parse.urlsplit(raw_url)
        if (parsed.scheme != "https" or parsed.hostname != "www.apple.com" or parsed.username is not None
                or parsed.password is not None or parsed.port is not None or parsed.query or parsed.fragment
                or not parsed.path.startswith("/shop/") or parsed.path == "/shop/checkout"):
            raise ValueError("product url must be an https://www.apple.com/shop/ URL with no query or fragment")
    for key in ("interval_seconds", "reminder_seconds"):
        n = value.get(key)
        if isinstance(n, bool) or not isinstance(n, int) or n < MIN_DELAY:
            raise ValueError(f"{key} must be an integer of at least {MIN_DELAY}")
    return value


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as file:
        return validate_config(json.load(file))


def parse_pickup(body: object, stores: dict[str, str]) -> dict[str, tuple[bool, str]]:
    payload = body.get("body") if isinstance(body, dict) else None
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list):
        raise ValueError("Apple response has no body.content list")
    result = {}
    for item in content:
        if not isinstance(item, dict):
            raise ValueError("Apple pickup entry is malformed")
        store_id = item.get("storeId")
        if not isinstance(store_id, str):
            raise ValueError("Apple pickup entry has no valid store ID")
        if store_id not in stores:
            continue
        if store_id in result:
            raise ValueError(f"Apple returned duplicate store {store_id}")
        quote = item.get("pickupSearchQuote")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError(f"Apple returned an empty pickup status for {store_id}")
        quote = quote.strip()
        if quote.casefold() in {"unavailable", "currently unavailable", "not available"}:
            result[store_id] = (False, quote)
        elif re.match(r"^Available(?:\s|$)", quote, re.IGNORECASE):
            result[store_id] = (True, quote)
        else:
            raise ValueError(f"Apple returned an unrecognized pickup status at {store_id}")
    missing = stores.keys() - result.keys()
    if missing:
        raise ValueError("Apple response omitted configured stores: " + ", ".join(sorted(missing)))
    return result


def query_product(product: dict, postal_code: str, stores: dict[str, str], session_factory=FetcherSession) -> dict:
    """Create a fresh Apple session for this product so its referer stays accurate."""
    try:
        with session_factory(timeout=TIMEOUT, retries=0, follow_redirects=False,
                             stealthy_headers=False, impersonate=None,
                             headers={"User-Agent": "AppleStockBark/1.0"}) as session:
            product_response = session.get(product["url"])
            _check_response(product_response)
            update = session.get(BASE + "/shop/address/location/update", params={"postalCode": postal_code})
            _check_response(update)
            address = update.json().get("body", {}).get("content", {}).get("address", {})
            if address.get("postalCode") != postal_code:
                raise AppleError("Apple did not confirm the configured postal code")
            params = [("product", product["part"])] + [(f"stores.{i}", sid) for i, sid in enumerate(stores)]
            pickup = session.get(BASE + "/shop/sba/pickup-detail", params=params)
            _check_response(pickup)
            return parse_pickup(pickup.json(), stores)
    except AppleError:
        raise
    except Exception as error:
        raise AppleError(f"Apple request failed ({type(error).__name__})") from None


def _check_response(response) -> None:
    status = response.status
    retry_after = response.headers.get("Retry-After") if response.headers else None
    try:
        retry_after = int(retry_after) if retry_after and retry_after.isdigit() else None
    except (ValueError, AttributeError):
        retry_after = None
    if status != 200:
        raise AppleError(f"Apple HTTP {status}", status, retry_after)


def bark_endpoint(raw: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise ValueError("BARK_URL must not contain control characters")
    url = urllib.parse.urlsplit(raw)
    path = url.path.strip("/")
    if (url.scheme != "https" or not url.hostname or not path or "/" in path or url.username is not None
            or url.password is not None or url.query or url.fragment):
        raise ValueError("BARK_URL must be an HTTPS Bark endpoint with one key path component")
    return urllib.parse.urlunsplit((url.scheme, url.netloc, "/" + path, "", ""))


def send_bark(endpoint: str, title: str, body: str, product_url: str | None = None) -> None:
    payload = {"title": title, "body": body, "group": "Apple Stock", "level": "timeSensitive"}
    if product_url:
        payload["url"] = product_url
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise BarkError(f"Bark HTTP {error.code}") from None
    except Exception as error:
        raise BarkError(f"Bark request failed ({type(error).__name__})") from None
    if not isinstance(data, dict) or data.get("code") != 200:
        raise BarkError("Bark rejected the notification")


def notify_available(config: dict, product: dict, availability: dict, state: dict,
                     endpoint: str, bark=send_bark, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    available = [sid for sid, (is_available, _) in availability.items() if is_available]
    for sid in availability:
        key = (product["part"], sid)
        if not availability[sid][0]:
            state.pop(key, None)
    due = [sid for sid in available if now - state.get((product["part"], sid), float("-inf")) >= config["reminder_seconds"]]
    if not due:
        return True
    places = ", ".join(f"{config['stores'][sid]} ({availability[sid][1]})" for sid in due)
    try:
        bark(endpoint, f"Apple pickup available: {product['name']}", places, product["url"])
    except Exception as error:
        print(f"Bark notification failed ({type(error).__name__}); will retry next cycle", file=sys.stderr, flush=True)
        return False
    for sid in due:
        state[(product["part"], sid)] = now
    return True


def run_once(config: dict, query=query_product, sleep_fn=time.sleep) -> int:
    errors = False
    any_available = False
    for index, product in enumerate(config["products"]):
        if index:
            sleep_fn(MIN_DELAY)
        try:
            availability = query(product, config["postal_code"], config["stores"])
        except Exception as error:
            detail = str(error) if isinstance(error, AppleError) else type(error).__name__
            print(f"ERROR {product['part']}: {detail}", file=sys.stderr)
            errors = True
            break
        found = [config["stores"][sid] for sid, (available, _) in availability.items() if available]
        any_available |= bool(found)
        print(f"{product['name']}: " + (", ".join(found) if found else "unavailable"))
    return 2 if errors else 10 if any_available else 0


def run_forever(config: dict, endpoint: str, query=query_product, bark=send_bark,
                sleep_fn=time.sleep) -> None:
    state = {}
    backoff = MIN_DELAY
    while True:
        try:
            for index, product in enumerate(config["products"]):
                if index:
                    sleep_fn(MIN_DELAY)
                availability = query(product, config["postal_code"], config["stores"])
                print(f"{product['name']}: " + ", ".join(
                    config["stores"][sid] + (" available" if yes else " unavailable")
                    for sid, (yes, _) in availability.items()), flush=True)
                notify_available(config, product, availability, state, endpoint, bark)
            backoff = MIN_DELAY
            sleep_fn(config["interval_seconds"])
        except AppleError as error:
            delay = max(min(MAX_BACKOFF, backoff), error.retry_after or 0)
            backoff = min(MAX_BACKOFF, max(MIN_DELAY, backoff * 2))
            print(f"Apple check failed ({error}); retry in {delay}s", file=sys.stderr, flush=True)
            sleep_fn(delay)
        except Exception as error:
            delay = backoff
            backoff = min(MAX_BACKOFF, max(MIN_DELAY, backoff * 2))
            print(f"Apple check failed ({type(error).__name__}); retry in {delay}s", file=sys.stderr, flush=True)
            sleep_fn(delay)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true")
    group.add_argument("--check-config", action="store_true")
    group.add_argument("--test-notify", action="store_true")
    args = parser.parse_args(argv)
    if args.test_notify:
        try:
            send_bark(bark_endpoint(os.environ["BARK_URL"]), "Apple stock monitor test", "Bark delivery is working")
            return 0
        except Exception as error:
            print(f"Bark test failed ({type(error).__name__})", file=sys.stderr)
            return 2
    try:
        config = load_config(args.config)
    except Exception as error:
        print(f"Config error ({type(error).__name__}): {error}", file=sys.stderr)
        return 2
    if args.check_config:
        print("Config valid")
        return 0
    if args.once:
        return run_once(config)
    try:
        run_forever(config, bark_endpoint(os.environ["BARK_URL"]))
    except KeyboardInterrupt:
        print("Stopped", flush=True)
    except Exception as error:
        print(f"Monitor error ({type(error).__name__})", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
