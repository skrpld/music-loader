"""Stand-in for pymongo on Android: see ../README.md (Android app only)."""


class MongoClient:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("MongoDB is not available on Android")


__all__ = ["MongoClient"]
