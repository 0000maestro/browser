def _pages():
    pages = []
    for tab in list_tabs():
        url = str(tab.get("url") or "")
        if url.startswith("http://") or url.startswith("https://"):
            pages.append(tab)
    return pages


def _visible(tab):
    switch_tab(tab, activate=False)
    return js("document.visibilityState") == "visible"


def _attrs(node):
    raw = node.get("attributes") or []
    return dict(zip(raw[0::2], raw[1::2]))


def _walk(node, found):
    if node.get("nodeName") == "IFRAME":
        src = _attrs(node).get("src") or ""
        if "challenges.cloudflare.com" in src and "turnstile" in src:
            backend = node.get("backendNodeId")
            if backend:
                found.append(backend)
    for child in node.get("children") or []:
        _walk(child, found)
    for child in node.get("shadowRoots") or []:
        _walk(child, found)
    content = node.get("contentDocument")
    if content:
        _walk(content, found)


def _turnstile_iframe():
    document = cdp("DOM.getDocument", depth=-1, pierce=True)
    found = []
    _walk(document.get("root") or {}, found)
    return found[0] if found else None


def _title():
    try:
        return str(js("document.title") or "")
    except RuntimeError:
        return ""


def _url():
    try:
        return str(js("location.href") or "")
    except RuntimeError:
        return ""


def _interstitial_title(title):
    folded = title.casefold().strip()
    return folded.startswith("just a moment") or folded.startswith("challenge") or folded == ""


def _cleared():
    try:
        if _interstitial_title(_title()):
            return False
        return _checkbox() is None
    except RuntimeError:
        return False


def _iframe_box(backend):
    try:
        box = cdp("DOM.getBoxModel", backendNodeId=backend)["model"]["content"]
    except RuntimeError as exc:
        if "box model" not in str(exc).lower():
            raise
        return None
    xs = box[0::2]
    ys = box[1::2]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    if right - left < 40 or bottom - top < 20:
        return None
    return {"x": left + 28, "y": (top + bottom) / 2, "width": right - left, "height": bottom - top}


def _checkbox():
    backend = _turnstile_iframe()
    if backend is None:
        return None
    return _iframe_box(backend)


def _click_checkbox():
    point = _checkbox()
    if point is None:
        return None
    click_at_xy(point["x"], point["y"])
    return point


def step_attach_visible_tab(ctx):
    chosen = None
    seen = []
    for tab in _pages():
        visible = _visible(tab)
        seen.append({"url": tab.get("url"), "visible": visible})
        if visible and chosen is None:
            chosen = tab
    if chosen is None:
        return {"ok": False, "observed": {"tabs": seen}}
    switch_tab(chosen, activate=False)
    info = page_info()
    return {
        "ok": True,
        "observed": {"url": info.get("url"), "title": info.get("title"), "tabs": seen},
    }


def _status():
    try:
        text = js("(() => { const el = document.getElementById('pSku8'); return el ? el.innerText : '' })()")
    except RuntimeError:
        return ""
    return str(text or "")


def _wait_until_clear(seconds):
    ticks = int(seconds / 0.5)
    for _ in range(ticks):
        if _cleared():
            return True
        wait(0.5)
    return _cleared()


def step_click_verify(ctx):
    if _cleared():
        return {"ok": True, "observed": {"title": _title(), "clicked": False}}

    point = None
    for _ in range(20):
        point = _click_checkbox()
        if point is not None:
            break
        wait(0.5)
    if point is None:
        return {"ok": False, "observed": {"title": _title(), "clicked": False, "status": _status()}}

    if _wait_until_clear(15):
        return {"ok": True, "observed": {"title": _title(), "url": _url(), "clicked": True, "point": point}}

    again = _click_checkbox()
    if again is not None:
        point = again

    if _wait_until_clear(20):
        return {"ok": True, "observed": {"title": _title(), "url": _url(), "clicked": True, "point": point}}

    return {
        "ok": False,
        "observed": {"title": _title(), "url": _url(), "clicked": True, "point": point, "status": _status()},
    }
