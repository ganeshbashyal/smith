import httpx


async def _get(url: str, headers: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def asx_quotes(symbols: list[str]) -> list[dict]:
    results = []
    for symbol in symbols:
        try:
            data = await _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.AX?range=2d&interval=1d")
            result = data["chart"]["result"][0]
            meta = result["meta"]
            results.append({"symbol": symbol, "price": meta.get("regularMarketPrice"), "currency": meta.get("currency"), "source": "Yahoo Finance chart"})
        except Exception as exc:
            results.append({"symbol": symbol, "error": str(exc), "source": "Yahoo Finance chart"})
    return results


async def next_f1_race() -> dict:
    try:
        data = await _get("https://api.jolpi.ca/ergast/f1/current/next.json")
        race = data["MRData"]["RaceTable"]["Races"][0]
        return {"race": race["raceName"], "round": race["round"], "date": race["date"], "circuit": race["Circuit"]["circuitName"], "source": "Jolpica F1"}
    except Exception as exc:
        return {"error": str(exc), "source": "Jolpica F1"}


async def football_matches(token: str | None) -> dict:
    if not token:
        return {"status": "not configured", "message": "Add a free football-data.org token to enable fixtures and results."}
    try:
        return await _get("https://api.football-data.org/v4/matches", {"X-Auth-Token": token})
    except Exception as exc:
        return {"error": str(exc), "source": "football-data.org"}
