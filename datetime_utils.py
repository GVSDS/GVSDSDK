import datetime


def UTCNow():
    return datetime.datetime.utcnow()


def ELTTokenExpiry(days=30):
    return UTCNow() + datetime.timedelta(days=days)


def FormatDateTime(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime.datetime):
        return dt.isoformat()
    return str(dt)


def IsTokenExpired(end_time):
    if end_time is None:
        return True
    naive = end_time.replace(tzinfo=None) if hasattr(end_time, 'tzinfo') else end_time
    return UTCNow() > naive
