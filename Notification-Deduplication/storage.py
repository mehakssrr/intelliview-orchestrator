import time

notification_store = {}

# Default time-to-live for dedup keys, in seconds (24 hours).
DEFAULT_TTL_SECONDS = 24 * 60 * 60


def save_notification(key, timestamp):
    notification_store[key] = timestamp


def notification_exists(key):
    return key in notification_store


def get_timestamp(key):
    return notification_store.get(key)


def cleanup_expired_keys(ttl_seconds=DEFAULT_TTL_SECONDS, now=None):

    current_time = now if now is not None else time.time()

    expired_keys = [
        key for key, ts in notification_store.items() if current_time - ts > ttl_seconds
    ]

    for key in expired_keys:
        del notification_store[key]

    return len(expired_keys)
