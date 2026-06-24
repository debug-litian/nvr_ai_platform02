import datetime

def timestamp_to_str(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def str_to_timestamp(s: str) -> float:
    return datetime.datetime.fromisoformat(s).timestamp()
