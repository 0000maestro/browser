#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "clear_turnstile",
    ROOT / "automations" / "clear_turnstile" / "run.py",
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class Scenario:
    def __init__(self, *, title: str | None, iframe_count: int):
        self.title = title
        self.iframe_count = iframe_count
        self.clicks = 0
        self.url = "https://example.test/"
        self.tab = {"url": self.url, "targetId": "page-1"}

    def list_tabs(self):
        return [self.tab]

    def switch_tab(self, tab, activate=False):
        assert tab is self.tab
        assert activate is False

    def js(self, expression):
        if expression == "document.visibilityState":
            return "visible"
        if expression == "document.title":
            return self.title
        if expression == "location.href":
            return self.url
        raise AssertionError(expression)

    def cdp(self, method, **params):
        if method == "DOM.getDocument":
            children = []
            for index in range(self.iframe_count):
                children.append(
                    {
                        "nodeName": "IFRAME",
                        "backendNodeId": 100 + index,
                        "attributes": [
                            "src",
                            f"https://challenges.cloudflare.com/turnstile/v0/api.js?widget={index}",
                        ],
                    }
                )
            return {"root": {"nodeName": "#document", "children": children}}
        if method == "DOM.getBoxModel":
            return {
                "model": {
                    "content": [100, 100, 400, 100, 400, 180, 100, 180],
                }
            }
        raise AssertionError(method)

    def click_at_xy(self, x, y):
        self.clicks += 1
        self.iframe_count = 0
        self.title = "Destination"

    def wait(self, seconds):
        assert seconds >= 0


def execute(scenario: Scenario):
    module.list_tabs = scenario.list_tabs
    module.switch_tab = scenario.switch_tab
    module.js = scenario.js
    module.cdp = scenario.cdp
    module.click_at_xy = scenario.click_at_xy
    module.wait = scenario.wait
    return module.step_click_verify({})


embedded = Scenario(title="Sign in", iframe_count=1)
result = execute(embedded)
assert result["ok"] is False
assert result["observed"]["clicked"] is False
assert embedded.clicks == 0

ambiguous = Scenario(title="Just a moment...", iframe_count=2)
result = execute(ambiguous)
assert result["ok"] is False
assert result["observed"]["clicked"] is False
assert ambiguous.clicks == 0

interstitial = Scenario(title="Just a moment...", iframe_count=1)
result = execute(interstitial)
assert result["ok"] is True
assert result["observed"]["clicked"] is True
assert interstitial.clicks == 1

already_clear = Scenario(title="Destination", iframe_count=0)
result = execute(already_clear)
assert result["ok"] is True
assert result["observed"]["clicked"] is False
assert already_clear.clicks == 0

print("clear_turnstile behavior validation passed")
