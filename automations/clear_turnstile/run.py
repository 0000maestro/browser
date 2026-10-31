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


def _recorded(tab, visible):
    return {"url": tab.get("url"), "visible": visible}


def _one_visible_tab():
    matches = []
    seen = []
    for tab in _pages():
        visible = _visible(tab)
        seen.append(_recorded(tab, visible))
        if visible:
            matches.append(tab)
    if len(matches) != 1:
        return None, seen
    switch_tab(matches[0], activate=False)
    return matches[0], seen


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


def _read(expression):
    try:
        value = js(expression)
    except RuntimeError:
        return None
    return "" if value is None else str(value)


def _title():
    return _read("document.title")


def _url():
    return _read("location.href")


def _interstitial_title(title):
    if title is None:
        return False
    folded = title.casefold().strip()
    return folded.startswith("just a moment") or folded.startswith("challenge")


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


def _iframe_state():
    try:
        backend = _turnstile_iframe()
    except RuntimeError:
        return "unreadable"
    if backend is None:
        return "absent"
    if _iframe_box(backend) is None:
        return "present"
    return "boxed"


def _cleared():
    try:
        if _turnstile_iframe() is not None:
            return False
    except RuntimeError:
        return False
    title = _title()
    if title is None:
        return True
    return not _interstitial_title(title)


def _click_checkbox():
    backend = _turnstile_iframe()
    if backend is None:
        return None
    point = _iframe_box(backend)
    if point is None:
        return None
    click_at_xy(point["x"], point["y"])
    return point


def _page():
    return {"url": _url(), "title": _title(), "iframe": _iframe_state()}


def step_attach_visible_tab(ctx):
    chosen, seen = _one_visible_tab()
    if chosen is None:
        return {"ok": False, "observed": {"tabs": seen}}
    return {"ok": True, "observed": {**_page(), "tabs": seen}}


def _wait_until_clear(seconds):
    ticks = int(seconds / 0.5)
    for _ in range(ticks):
        if _cleared():
            return True
        wait(0.5)
    return _cleared()


def step_click_verify(ctx):
    chosen, seen = _one_visible_tab()
    if chosen is None:
        return {"ok": False, "observed": {"tabs": seen, "clicked": False}}

    if _cleared():
        return {"ok": True, "observed": {**_page(), "clicked": False, "tabs": seen}}

    point = None
    for _ in range(20):
        point = _click_checkbox()
        if point is not None:
            break
        wait(0.5)
    if point is None:
        return {"ok": False, "observed": {**_page(), "clicked": False, "tabs": seen}}

    if _wait_until_clear(20):
        return {"ok": True, "observed": {**_page(), "clicked": True, "point": point, "tabs": seen}}

    return {"ok": False, "observed": {**_page(), "clicked": True, "point": point, "tabs": seen}}
