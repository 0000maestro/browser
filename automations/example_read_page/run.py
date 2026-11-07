from urllib.parse import urlparse


def step_open_page(ctx):
    target = ctx["inputs"]["url"]
    new_tab(target)
    wait_for_load()
    current = str(js("location.href"))
    expected = urlparse(target)
    actual = urlparse(current)
    return {
        "ok": actual.scheme == expected.scheme and actual.netloc == expected.netloc,
        "observed": {"target": target, "current_url": current},
    }


def step_read_title(ctx):
    title = str(js("document.title") or "").strip()
    return {
        "ok": bool(title),
        "observed": {"title": title},
    }
