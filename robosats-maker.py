import asyncio
import requests
import json
from requests.exceptions import HTTPError
import socks
import socket
import hashlib
import aiohttp
import pytz
import sys
import argparse
from datetime import datetime, timedelta


def hash_token_as_base91(token):
    # Hashing the token using SHA-256
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    # Encoding the hash in Base91
    return encode_base91(digest)


def encode_base91(input_bytes):
    base91_alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"'
    buffer = 0
    buffer_length = 0
    output = []

    for byte in input_bytes:
        buffer |= (byte & 255) << buffer_length
        buffer_length += 8

        if buffer_length > 13:
            value = buffer & 8191
            if value > 88:
                buffer >>= 13
                buffer_length -= 13
            else:
                value = buffer & 16383
                buffer >>= 14
                buffer_length -= 14

            output.append(base91_alphabet[value % 91])
            output.append(base91_alphabet[value // 91])

    if buffer_length != 0:
        output.append(base91_alphabet[buffer % 91])
        if buffer_length > 7 or buffer > 90:
            output.append(base91_alphabet[buffer // 91])

    return "".join(output)


# Constants for the Robosats URLs
ROBOSATS_MAINNET = "robosats6tkf3eva7x2voqso3a5wcorsnw34jveyxfqi2fu7oyheasid.onion"


# Configure requests to use Tor
def configure_tor_requests():
    socks.set_default_proxy(socks.SOCKS5, "localhost", 9050)
    socket.socket = socks.socksocket


# Configure requests session to use Tor
def get_tor_session():
    session = requests.session()
    session.proxies = {
        "http": "socks5h://localhost:9050",
        "https": "socks5h://localhost:9050",
    }
    return session


# Asynchronous function to get info
async def get_info(token):
    return await make_general_request("info", token, {}, {}, "GET")


# Asynchronous function to make an order
async def make_order(
    token,
    type,
    currency,
    has_range,
    min_amount,
    max_amount,
    amount,
    payment_method,
    premium,
    public_duration,
    escrow_duration,
    bond_size,
):
    form_body_params = {
        "type": type,  # 0: buy, 1: sell
        "currency": currency,  # 1: usd, 2: eur, 3: jpy, 4: gbp, 5: aud, 6: cad (https://github.com/RoboSats/robosats/blob/main/frontend/static/assets/currencies.json)
        "amount": amount,
        "payment_method": payment_method,
        "is_explicit": "false",
        "premium": premium,
        "public_duration": str(public_duration),
        "escrow_duration": str(escrow_duration),
        "bond_size": bond_size,
        "has_range": has_range,
        "min_amount": min_amount,
        "max_amount": max_amount,
    }

    return await make_general_request("make", token, {}, form_body_params, "POST")


# Asynchronous function to make a general request
async def make_general_request(api, token, query_params, form_body_params, method):
    host = ROBOSATS_MAINNET
    url = f"http://{host}/api/{api}/"
    session = get_tor_session()

    headers = {}
    if token:
        hashed_token = hash_token_as_base91(token)
        headers[
            "Authorization"
        ] = f"Token {hashed_token}"  # Modify this line if the token needs hashing

    if method.upper() == "POST" and form_body_params:
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    response = session.request(
        method, url, headers=headers, params=query_params, data=form_body_params
    )
    response.raise_for_status()
    return json.loads(response.text)


async def get_order_details(token, order_id):
    return await make_general_request(
        "order", token, {"order_id": str(order_id)}, {}, "GET"
    )


async def cancel_order(token, order_id):
    return await perform_order_action(token, order_id, "cancel")


async def perform_order_action(token, order_id, action):
    form_body_params = {
        "action": action,
    }

    return await make_general_request(
        "order", token, {"order_id": str(order_id)}, form_body_params, "POST"
    )


async def pay_bond_invoice(auth_token, invoice, wallet_id):
    url = "https://api.blink.sv/graphql"
    headers = {"Content-Type": "application/json", "X-API-KEY": auth_token}
    data = {
        "query": "mutation LnInvoicePaymentSend($input: LnInvoicePaymentInput!) {\n  lnInvoicePaymentSend(input: $input) {\n    status\n    errors {\n      message\n      path\n      code\n    }\n  }\n}",
        "variables": {"input": {"paymentRequest": invoice, "walletId": wallet_id}},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url, headers=headers, data=json.dumps(data)
        ) as response:
            if response.status == 200:
                response_data = await response.json()
                return response_data
            else:
                return {
                    "error": "Failed to pay invoice",
                    "status_code": response.status,
                }


async def get_wallet_info(auth_token):
    url = "https://api.blink.sv/graphql"
    headers = {"Content-Type": "application/json", "X-API-KEY": auth_token}
    data = {
        "query": "query me { me { defaultAccount { wallets { id walletCurrency balance }}}}",
        "variables": {},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data) as response:
            if response.status == 200:
                response_data = await response.json()
                return response_data
            else:
                return {
                    "error": "Failed to retrieve wallet info",
                    "status_code": response.status,
                }


def parse_arguments():
    parser = argparse.ArgumentParser(description="RoboSats Maker Script")
    parser.add_argument("robosats_token", help="RoboSats token")
    parser.add_argument("blink_api_key", help="Blink API key")
    parser.add_argument(
        "--has_range",
        default=False,
        type=bool,
        help="If will use range of value (default: False)",
    )
    parser.add_argument(
        "--min_amount", default=None, type=int, help="Minimum value for trade"
    )
    parser.add_argument(
        "--max_amount", type=int, default=None, help="Max value for trade"
    )
    parser.add_argument("--amount", default=None, type=int, help="Amount for the order")
    parser.add_argument("payment_method", type=str, help="Payment Method")
    parser.add_argument("premium", type=float, help="Premium for the order")
    parser.add_argument(
        "--type",
        type=int,
        default=0,
        choices=[0, 1],
        help="Order type: 0 for buy, 1 for sell (default: 0)",
    )
    parser.add_argument(
        "--currency",
        type=int,
        default=2,
        choices=range(1, 77),
        help="Currency: 1 for USD, 2 for EUR, etc. (default: 2), more details: https://github.com/RoboSats/robosats/blob/main/frontend/static/assets/currencies.json",
    )
    return parser.parse_args()


async def main():
    args = parse_arguments()

    robosats_token = args.robosats_token
    blink_api_key = args.blink_api_key
    amount = args.amount
    has_range = args.has_range
    min_amount = args.min_amount
    max_amount = args.max_amount
    premium = args.premium
    payment_method = args.payment_method


    # 0: buy, 1: sell
    order_type = args.type
    # 1: usd, 2: eur, 3: jpy, 4: gbp, 5: aud, 6: cad (https://github.com/RoboSats/robosats/blob/main/frontend/static/assets/currencies.json)
    currency = args.currency

    get_info_result = await get_info(robosats_token)
    print(f"Info Result: {get_info_result}")

    # Get wallet information
    wallet_info = await get_wallet_info(blink_api_key)
    print("Wallet Information:", wallet_info)

    # Extract BTC wallet ID (if needed)
    btc_wallet_id = None
    if (
        wallet_info
        and "data" in wallet_info
        and "me" in wallet_info["data"]
        and "defaultAccount" in wallet_info["data"]["me"]
    ):
        wallets = wallet_info["data"]["me"]["defaultAccount"]["wallets"]
        for wallet in wallets:
            if wallet["walletCurrency"] == "BTC":
                btc_wallet_id = wallet["id"]
                break

    print("BTC Wallet ID:", btc_wallet_id)

    order_id = None

    while True:
        utc_time = datetime.now(pytz.timezone("America/Sao_Paulo"))
        hour = utc_time.hour

        print(f"hour: {hour}")

        if hour == 6 and order_id is None:
            print(f"Making order at {utc_time}")
            order_result = await make_order(
                token=robosats_token,
                type=order_type,
                currency=currency,
                amount=amount,
                has_range=has_range,
                min_amount=min_amount,
                max_amount=max_amount,
                payment_method=payment_method,
                premium=premium,
                public_duration=15 * 60 * 60,
                escrow_duration=3 * 60 * 60,
                bond_size="3.0",
            )
            print("Order Result:", order_result)
            order_id = order_result["id"]

            order_details = await get_order_details(robosats_token, order_id)
            print("Order Details:", order_details)

            bond_invoice = order_details["bond_invoice"]
            print("Bond Invoice:", bond_invoice)

            payment_result = await pay_bond_invoice(
                blink_api_key, bond_invoice, btc_wallet_id
            )
            print("Payment Result:", payment_result)

        elif hour == 23 and order_id is not None:
            print(f"Cancelling order at {utc_time}")
            try:
                cancel_result = await cancel_order(robosats_token, order_id)
                print(f"Cancel Order Result: {cancel_result}")
            except HTTPError as http_err:
                # Cancel order is expected to fail
                print("Cancel Order may have succeeded")
            order_id = None  # Reset order ID after cancellation

        # Wait for 60 seconds before next check
        await asyncio.sleep(60)


# Run the main function
asyncio.run(main())
