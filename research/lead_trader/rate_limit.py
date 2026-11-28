"""Durable request budgeting for public portfolio reads."""

import math
import re
from email.utils import parsedate_to_datetime


LOCAL = ((60, 6), (3_600, 90), (86_400, 800))
MIN_PACE = 12.
UNITS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def reset_at(value, now):
    n = number(value)
    if n is not None and n >= 0:
        if n > 10**12:
            return n / 1000
        return n if n > 10**9 else now + n
    if value:
        try:
            return parsedate_to_datetime(value).timestamp()
        except (ValueError, TypeError, OverflowError):
            pass
    return None


def limits(headers, now, weights):
    result = {}
    # Structured RateLimit and RateLimit-Policy, plus legacy limit headers.
    policies = {name: (int(quota), int(window)) for name, quota, window in re.findall(
        r'"([^"]+)"\s*;\s*q=(\d+)\s*;\s*w=(\d+)', headers.get("ratelimit-policy", ""), re.I)}
    result.update({name: {"limit": quota, "remaining": None, "reset": now + window, "window": window}
                   for name, (quota, window) in policies.items() if quota > 0 and window > 0})
    for name, remaining, seconds in re.findall(
        r'"([^"]+)"\s*;\s*r=(\d+)\s*;\s*t=(\d+)', headers.get("ratelimit", ""), re.I):
        quota, window = policies.get(name, (None, None))
        result[name] = {"limit": quota, "remaining": int(remaining), "reset": now + int(seconds), "window": window}
    for prefix in ("ratelimit-", "x-ratelimit-", "x-rate-limit-"):
        remain = number(headers.get(prefix + "remaining"))
        limit = number(headers.get(prefix + "limit"))
        reset = reset_at(headers.get(prefix + "reset"), now)
        if remain is not None:
            result[prefix.rstrip("-")] = {"limit": limit, "remaining": remain, "reset": reset or now + 3600,
                                           "window": None}
    for name, used in headers.items():
        found = re.fullmatch(r"x-mbx-used-weight-(\d+)([smhd])", name)
        if found and number(used) is not None:
            window = int(found[1]) * UNITS[found[2]]
            cap = weights.get(name)
            result[name] = {"limit": cap, "used": number(used),
                            "remaining": max(0., cap - number(used)) if cap is not None else None,
                            "reset": (int(now) // window + 1) * window, "window": window}
    return result


def budget(db, state, now, settings):
    """Return next allowed time and inspectable budget state, including uncompleted attempts."""
    when = max(now, state.get("cooldown", 0), state.get("next_pace", 0))
    if when > now:
        blockers = [state.get("last_reason") or "request pacing"]
    else:
        blockers = []
    local = []
    margin = settings["margin"]
    for seconds, cap in LOCAL:
        used = [r[0] for r in db.execute("SELECT started FROM requests WHERE started>? ORDER BY started", (now - seconds,))]
        soft = max(1, int(cap * (1. - margin)))
        if len(used) >= soft:
            when = max(when, used[len(used) - soft] + seconds + .1)
            blockers.append(f"local {seconds}s budget")
        local.append(dict(seconds=seconds, limit=cap, safety_limit=soft, used=len(used),
                          remaining=max(0, cap-len(used))))
    remote = {name: value for name, value in state.get("remote", {}).items() if value["reset"] > now}
    for name, policy in remote.items():
        remain, cap = policy.get("remaining"), policy.get("limit")
        reserve = max(1, int((cap or 0) * margin))
        if remain is not None and remain < reserve + settings["max_request_cost"]:
            when = max(when, policy["reset"] + 2.)
            blockers.append(name)
        elif remain is None and cap and policy.get("window"):
            seconds = policy["window"]
            soft = max(1, int((cap - reserve) / settings["max_request_cost"]))
            timestamps = [row[0] for row in db.execute(
                "SELECT started FROM requests WHERE started>? ORDER BY started", (now - seconds,)
            )]
            if len(timestamps) >= soft:
                when = max(when, timestamps[len(timestamps) - soft] + seconds + 2.)
                blockers.append(f"{name} estimated from local traffic")
    return when, local, remote, blockers


def reserve(state, now, cost):
    for policy in state.get("remote", {}).values():
        if policy["reset"] > now and policy["remaining"] is not None:
            policy["remaining"] = max(0., policy["remaining"] - cost)
    state["next_pace"] = now + state.get("pace", MIN_PACE)


def feedback(state, http, headers, payload, now, settings):
    state["remote"] = {name: policy for name, policy in state.get("remote", {}).items() if policy["reset"] > now}
    state["remote"].update(limits(headers, now, settings["weights"]))
    code = str(payload.get("code", ""))
    retry = max((at for at in (reset_at(headers.get("retry-after"), now),
                                reset_at(payload.get("retryAfter"), now),
                                reset_at(payload.get("data", {}).get("retryAfter"), now)
                                if isinstance(payload.get("data"), dict) else None) if at is not None), default=0.)
    if http in (401, 403, 451):
        state["paused"] = True
        state["last_reason"] = f"HTTP {http} access restriction"
        return "restricted"
    if http in (418, 429) or code in {"90801003", "-1003"}:
        strikes = state.get("throttles", 0) + 1
        state["throttles"] = strikes
        state["pace"] = min(300., max(30., state.get("pace", MIN_PACE) * 2))
        fallback = (86_400 if http == 418 else min(21_600, 1800 * 2 ** min(strikes - 1, 4)))
        state["cooldown"] = max(state.get("cooldown", 0), retry + 3 if retry > now else now + fallback)
        state["last_reason"] = f"exchange throttle HTTP {http}, code {code}"
        return "throttled"
    if http is None or http >= 500 or code in {"11012005", "-1007"}:
        state["pace"] = min(300., max(MIN_PACE, state.get("pace", MIN_PACE) * 1.5))
        state["cooldown"] = max(state.get("cooldown", 0), now + state["pace"], retry + 2 if retry > now else 0)
        state["last_reason"] = "temporary service or transport failure"
        return "retry"
    if http == 200 and code == "000000":
        state["pace"] = max(MIN_PACE, state.get("pace", MIN_PACE) * .99)
        if retry > now:
            state["cooldown"] = max(state.get("cooldown", 0), retry + 2)
            state["last_reason"] = "server-requested delay"
        else:
            state["last_reason"] = None
        return "ok"
    if retry > now:
        state["cooldown"] = max(state.get("cooldown", 0), retry + 2)
        state["last_reason"] = "server-requested delay"
        return "retry"
    return "error"
