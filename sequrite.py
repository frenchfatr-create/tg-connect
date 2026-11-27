
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data, bot_token, max_age=3600):
    if not init_data or len(init_data) > 16384:
        return None

    try:
        pairs = dict(parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True
        ))

        received_hash = pairs.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(pairs.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            return None

        auth_date = int(pairs.get("auth_date", "0"))
        now = int(time.time())

        if auth_date <= 0 or auth_date > now + 30:
            return None

        if now - auth_date > max_age:
            return None

        user = json.loads(pairs.get("user", "{}"))

        if not isinstance(user, dict) or not user.get("id"):
            return None

        return user

    except (ValueError, TypeError, json.JSONDecodeError):
        return None
