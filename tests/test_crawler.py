"""🔄 Rastreador de catálogo completo: cursor persistente, fin de catálogo, 1er barrido silencioso, bloqueos."""
import pytest

import config
import crawler as C
from fetchers import BlockedError
from models import PageResult, Product
from storage import PriceStore


class FakeScraper:
    """Catálogo simulado: pages[n] = lista de (id, precio, precio_normal). Más allá -> página vacía."""
    pages: dict = {}
    blocked = False
    total = None

    def page_url(self, base, n):
        return f"{base}?page={n}"

    async def fetch(self, ctx, url):
        if FakeScraper.blocked:
            raise BlockedError("403")
        n = int(url.rsplit("=", 1)[1])
        items = FakeScraper.pages.get(n, [])
        prods = [Product(f"falabella:{i}", "falabella", f"Notebook Lenovo IdeaPad {i}", f"https://f.cl/{i}", p, lp)
                 for i, p, lp in items]
        return PageResult(prods, "fake", FakeScraper.total)


SRC = config.Source("Falabella · Notebooks", "https://f.cl/nb", "falabella", 2, True, False, "notebooks")


@pytest.fixture
def mon(tmp_path, monkeypatch):
    from monitor import Monitor
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "crawl.db")
    monkeypatch.setitem(C.SCRAPERS, "falabella", FakeScraper)
    FakeScraper.pages, FakeScraper.blocked, FakeScraper.total = {}, False, None
    m = Monitor(dry_run=True)
    m.sent = []

    async def fake_dispatch(store, alerts, ts, report=None):
        m.sent.extend(alerts)
        return alerts
    monkeypatch.setattr(m, "dispatch", fake_dispatch)
    return m


def meta(k):
    with PriceStore(config.DB_PATH) as s:
        return s.get_meta(k)


def test_crawl_sources_skip_traps_and_single_page_stores():
    groups = C.crawl_sources(config.SOURCES)
    names = [s.name for srcs in groups.values() for s in srcs]
    assert names and not any(m in n for n in names for m in ("⚡", "🆕"))
    assert all(s.site in C.CRAWLABLE for srcs in groups.values() for s in srcs)
    assert "www.falabella.com" in groups and len(groups["www.falabella.com"]) >= 9   # todas las categorías


@pytest.mark.asyncio
async def test_full_sweep_cursor_and_quiet_first_pass(mon):
    FakeScraper.pages = {1: [(1, 500000, None)], 2: [(2, 600000, None)], 3: [(3, 99990, 999990)]}  # pág 3: ¿error?
    cr = C.Crawler(mon)
    for expected_next in (2, 3, 4):
        await cr._step(SRC)
        assert meta(f"crawl:{SRC.name}")["page"] == expected_next
    assert mon.sent == []                                   # 1er barrido: productos nuevos sin alertas
    await cr._step(SRC)                                     # pág 4 vacía -> fin del catálogo
    sweep = meta(f"sweep:{SRC.name}")
    assert sweep and sweep["pages"] == 3 and meta(f"crawl:{SRC.name}")["page"] == 1
    # 2º barrido: aparece un producto nuevo con un error en la página 2 -> SÍ alerta
    FakeScraper.pages[2].append((9, 129990, 1299990))
    await cr._step(SRC)
    await cr._step(SRC)
    assert [a.kind for a in mon.sent] == ["price_error"] and mon.sent[0].obs.product.key == "falabella:9"


@pytest.mark.asyncio
async def test_total_pages_ends_sweep(mon):
    FakeScraper.pages = {1: [(1, 500000, None)], 2: [(2, 500000, None)]}
    FakeScraper.total = 2
    cr = C.Crawler(mon)
    await cr._step(SRC)
    await cr._step(SRC)
    assert meta(f"sweep:{SRC.name}")["pages"] == 2 and meta(f"crawl:{SRC.name}")["page"] == 1


@pytest.mark.asyncio
async def test_repeated_page_means_end(mon):
    FakeScraper.pages = {1: [(1, 500000, None)], 2: [(2, 500000, None)], 3: [(2, 500000, None)]}  # la tienda repite
    cr = C.Crawler(mon)
    for _ in range(3):
        await cr._step(SRC)
    assert meta(f"sweep:{SRC.name}") is not None


@pytest.mark.asyncio
async def test_block_pauses_store_and_keeps_cursor(mon):
    FakeScraper.pages = {1: [(1, 500000, None)]}
    cr = C.Crawler(mon)
    FakeScraper.blocked = True
    await cr._step(SRC)
    assert meta("block:falabella")["strikes"] == 1 and meta(f"crawl:{SRC.name}") is None
    with PriceStore(config.DB_PATH) as s:
        assert mon._paused(s, SRC)                          # el trabajador dejará de pedirle a la tienda


@pytest.mark.asyncio
async def test_sponsored_item_on_every_page_does_not_end_sweep(mon):
    # Falabella: el mismo producto patrocinado (id 99) encabeza TODAS las páginas; no es el fin del catálogo
    FakeScraper.pages = {n: [(99, 300000, None)] + [(n * 10 + i, 500000, None) for i in range(5)] for n in (1, 2, 3)}
    cr = C.Crawler(mon)
    for _ in range(3):
        await cr._step(SRC)
    assert meta(f"sweep:{SRC.name}") is None and meta(f"crawl:{SRC.name}")["page"] == 4
    await cr._step(SRC)                                     # pág 4 vacía -> ahora sí termina
    assert meta(f"sweep:{SRC.name}")["pages"] == 3


@pytest.mark.asyncio
async def test_crawler_yields_to_fast_cycles(mon):
    import asyncio
    mon.cycle_busy = True
    FakeScraper.pages = {1: [(1, 500000, None)]}
    stop = asyncio.Event()
    task = asyncio.create_task(C.Crawler(mon)._worker("f.cl", [SRC], stop))
    await asyncio.sleep(0.3)
    assert meta(f"crawl:{SRC.name}") is None                # no avanzó mientras el ciclo estaba ocupado
    stop.set()
    await task
