"""Fail-closed, versioned exchange calendar loaded from verified fixed dates."""
import json
from pathlib import Path
from datetime import datetime, date, time, timedelta, timezone
from zoneinfo import ZoneInfo

DATA = json.loads((Path(__file__).parent/'calendars.json').read_text())


def _market(market):
    return DATA['markets'][market]


def is_session(day, market):
    m = _market(market)
    if not DATA['valid_from'] <= day.isoformat() <= DATA['valid_through']:
        raise ValueError('CALENDAR_EXPIRED_OR_OUT_OF_RANGE: '+day.isoformat())
    return day.weekday()<5 and day.isoformat() not in m['holidays']


def session_close(day, market):
    if not is_session(day,market):
        raise ValueError('not a session: '+day.isoformat())
    m = _market(market)
    if market=='us':
        hh,mm=(13,0) if day.isoformat() in m['early_closes'] else (16,0)
    else:
        hh,mm=(15,0) if day<date(2024,11,5) else (15,30)
    return datetime.combine(day,time(hh,mm),ZoneInfo(m['timezone']))


def expected_latest(now, market, delay_minutes=30):
    if now.tzinfo is None:
        raise ValueError('now must include timezone')
    d=now.astimezone(ZoneInfo(_market(market)['timezone'])).date()
    for _ in range(15):
        if is_session(d,market) and now>=session_close(d,market)+timedelta(minutes=delay_minutes):
            return d
        d-=timedelta(days=1)
    raise ValueError('no completed exchange session within 15 days')


def sessions(start,end,market):
    result=[]
    d=start
    while d<=end:
        if is_session(d,market): result.append(d.isoformat())
        d+=timedelta(days=1)
    return result


def session_is_open(now,market):
    """Regular stock sessions only; Tokyo lunch is inactive."""
    m=_market(market);local=now.astimezone(ZoneInfo(m['timezone']));d=local.date()
    if not is_session(d,market):return False
    close=session_close(d,market)
    if market=='us':
        opened=datetime.combine(d,time(9,30),ZoneInfo(m['timezone']))
        return opened<=local<close
    morning=datetime.combine(d,time(9,0),ZoneInfo(m['timezone']))
    lunch=datetime.combine(d,time(11,30),ZoneInfo(m['timezone']))
    afternoon=datetime.combine(d,time(12,30),ZoneInfo(m['timezone']))
    return morning<=local<lunch or afternoon<=local<close
