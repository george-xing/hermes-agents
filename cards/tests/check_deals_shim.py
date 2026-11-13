"""Live check of DEALS_SHIM_JS in the cards Chrome, without any bank or the real Deals site.

Opens a throwaway background tab, answers every https://deals.merchant-rewards.com/* request from a
local mock (Fetch interception: nothing leaves the machine), and loads the mock three times: plain, with
the shim the way open_deals() adds it (Page.enable first), and with the shim but no Page.enable, which
Chrome silently ignores (the 2026-11-13 live failure). A fourth load puts the tab in front: with the
screen in use it renders and the shim must stay native; behind the lock screen it is "visible" but
draws nothing, and the shim must detect the stall. The output says which of the two it exercised. The mock gates each section's offer fetch on an IntersectionObserver, like the real
site, and its Activate button flips to "Activated" in a requestAnimationFrame callback. Closes the tab.

    ~/.hermes/hermes-agent/.venv/bin/python cards/tests/check_deals_shim.py [cdp_http_url]

Not part of unittest discovery: it needs the running cards Chrome (port 19223) behind the lock screen,
where tabs are hidden. Exit status 0 means: without the shim (or without Page.enable) the sections stay
Loading; with it the tiles render and activation completes.
"""
import asyncio
import base64
import json
from pathlib import Path
import sys
import urllib.request

import websockets

sys.path.insert(0, str(Path(__file__).parents[1] / "bin"))
import cards_browser as cb  # noqa: E402
import cards_journal  # noqa: E402

MOCK_PAGE = """<!doctype html><html><head><title>Discover our Offers</title></head><body>
<h2>Welcome</h2>
<section data-testid="section-featured"><h3>Featured Deals</h3><p class="loading">Loading Featured Deals</p><div class="list"></div></section>
<section data-testid="section-essentials"><h3>Daily Essentials</h3><p class="loading">Loading Daily Essentials</p><div class="list"></div></section>
<script>
for (const section of document.querySelectorAll('section')) {
  const io = new IntersectionObserver(async ([entry]) => {
    if (!entry.isIntersecting) return;
    io.disconnect();
    const offers = await (await fetch('/mock/offers.json?s=' + section.dataset.testid)).json();
    section.querySelector('.loading').remove();
    for (const o of offers) {
      const tile = document.createElement('div');
      tile.dataset.testid = 'offerTile-' + o.id;
      tile.innerHTML = '<span>' + o.merchant + '</span><button>Activate</button>';
      tile.querySelector('button').onclick = (e) => requestAnimationFrame(() => { e.target.textContent = 'Activated'; });
      section.querySelector('.list').append(tile);
    }
  }, {threshold: 0.5});
  io.observe(section);
}
</script></body></html>"""
MOCK_OFFERS = json.dumps([{"id": "m1", "merchant": "Mock Coffee"}, {"id": "m2", "merchant": "Mock Books"}])


async def load(cdp_http, with_shim, page_enable=True, foreground=False):
    ws_url = json.load(urllib.request.urlopen(cdp_http + "/json/version"))["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url, max_size=None) as ws:
        counter, pending = 0, {}

        async def send(method, params=None, session=None):
            nonlocal counter
            counter += 1
            future = asyncio.get_running_loop().create_future()
            pending[counter] = future
            await ws.send(json.dumps({"id": counter, "method": method, "params": params or {},
                                      **({"sessionId": session} if session else {})}))
            return await asyncio.wait_for(future, 30)

        async def reader():
            async for raw in ws:
                message = json.loads(raw)
                if message.get("id") in pending:
                    pending.pop(message["id"]).set_result(message.get("result", message.get("error")))
                elif message.get("method") == "Fetch.requestPaused":
                    params = message["params"]
                    url = params["request"]["url"]
                    body, kind = (MOCK_OFFERS, "application/json") if "/mock/offers.json" in url else (MOCK_PAGE, "text/html")
                    asyncio.ensure_future(send("Fetch.fulfillRequest", {
                        "requestId": params["requestId"], "responseCode": 200,
                        "responseHeaders": [{"name": "Content-Type", "value": kind}],
                        "body": base64.b64encode(body.encode()).decode()}, message.get("sessionId")))

        task = asyncio.create_task(reader())
        target = (await send("Target.createTarget", {"url": "about:blank", "background": not foreground}))["targetId"]
        try:
            session = (await send("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]
            await send("Fetch.enable", {"patterns": [{"urlPattern": cards_journal.DEALS_ORIGIN + "/*"}]}, session)
            if foreground:
                await send("Target.activateTarget", {"targetId": target})
            if page_enable:
                await send("Page.enable", session=session)
            if with_shim:
                await send("Page.addScriptToEvaluateOnNewDocument", {"source": cb.DEALS_SHIM_JS}, session)
            await send("Page.navigate", {"url": cards_journal.DEALS_ORIGIN + "/?ff-upside=true"}, session)
            await asyncio.sleep(4)

            async def evaluate(expression):
                reply = await send("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                        "awaitPromise": True}, session)
                return reply["result"].get("value")  # undefined has no value

            hidden = await evaluate("document.visibilityState")
            shim = await evaluate("window.__cardsShim || null")
            probe = await evaluate(cb.DEALS_PROBE_JS)
            activated = None
            if probe.get("tiles"):
                await evaluate("document.querySelector('[data-testid^=\"offerTile-\"] button').click()")
                await asyncio.sleep(1)
                activated = await evaluate("document.querySelector('[data-testid^=\"offerTile-\"] button').textContent")
            return {"visibility": hidden, "verdict": cards_journal.classify_deals(probe), "probe": probe,
                    "activation": activated, "shim": shim and {k: shim[k] for k in ("mode", "reason", "fired")}}
        finally:
            await send("Target.closeTarget", {"targetId": target})
            task.cancel()


async def main(cdp_http):
    plain = await load(cdp_http, with_shim=False)
    shimmed = await load(cdp_http, with_shim=True)
    unenabled = await load(cdp_http, with_shim=True, page_enable=False)
    front = await load(cdp_http, with_shim=True, foreground=True)
    print("without shim:", plain)
    print("with shim:   ", shimmed)
    print("shim, no Page.enable:", unenabled)
    print("shim, front tab:", front)
    front_case = ("visible but not drawn (stall detected)" if front["shim"]["mode"] == "stalled"
                  and front["visibility"] == "visible" else
                  "rendering normally (shim stayed native)" if front["shim"]["mode"] == "native" else "unexpected")
    print("front tab exercised:", front_case)
    if plain["visibility"] != "hidden":
        print("SKIP: the tab is visible (someone is viewing the screen), so the native observer works anyway.")
        return 0
    ok = (plain["verdict"] == "loading" and not plain["probe"]["tiles"]
          and shimmed["verdict"] == "ready" and shimmed["probe"]["tiles"] == 4 and shimmed["activation"] == "Activated"
          and unenabled["verdict"] == "loading" and not unenabled["probe"]["tiles"]
          and shimmed["shim"]["mode"] == "stalled" and front["verdict"] == "ready"
          and front["activation"] == "Activated" and front_case != "unexpected")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:19223")))
