from zoneinfo import ZoneInfo

UTC = ZoneInfo('UTC')

def timezone(name: str):
    return ZoneInfo(name)
