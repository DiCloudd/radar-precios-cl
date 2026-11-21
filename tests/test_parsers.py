"""Cada extractor contra un fixture mínimo con la estructura real verificada (nov-2026)."""
import json

from scrapers import SCRAPERS


def parse(site, html, url="https://x.cl/"):
    return SCRAPERS[site]().parse(html, url)


def test_falabella_next_data():
    nd = {"props": {"pageProps": {"pagination": {"count": 100, "perPage": 48}, "results": [{
        "productId": "143212645", "skuId": "154443411", "displayName": "IPhone 13 128GB", "brand": "APPLE",
        "url": "https://www.falabella.com/falabella-cl/product/143212645/x",
        "prices": [{"type": "cmrPrice", "price": ["329.990"], "crossed": False},
                   {"type": "eventPrice", "price": ["339.990"], "crossed": False},
                   {"type": "normalPrice", "price": ["499.990"], "crossed": True}]}]}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
    r = parse("falabella", html)
    p = r.products[0]
    assert (p.key, p.price, p.card_price, p.list_price, r.total_pages) == ("falabella:143212645", 339990, 329990, 499990, 3)


def test_falabella_dom_fallback():
    html = ('<a data-pod="catalyst-pod" data-key="99" href="https://f.cl/product/99/x?a=1"><img alt="Moto G">'
            '<li data-event-price="100.990"></li><li data-normal-price="150.990"></li><li data-cmr-price="90.990"></li></a>')
    p = parse("falabella", html).products[0]
    assert (p.key, p.price, p.list_price, p.card_price, p.url) == ("falabella:99", 100990, 150990, 90990, "https://f.cl/product/99/x")


def test_paris_json_ld_and_cards():
    ld = {"@type": "WebPage", "mainEntity": {"@type": "ItemList", "itemListElement": [{"item": {
        "@type": "Product", "name": "Samsung Galaxy A57", "url": "https://www.paris.cl/samsung-a57-MKS5469055.html",
        "brand": {"name": "Samsung"}, "offers": {"price": 399990}}}]}}
    html = (f'<script type="application/ld+json">{json.dumps(ld)}</script>'
            '<div data-cnstrc-item-id="MKS5469055" data-cnstrc-item-price="399990">37% $379.990 33% $399.990 $599.990</div>')
    p = parse("paris", html).products[0]
    assert (p.key, p.price, p.card_price, p.list_price) == ("paris:MKS5469055", 399990, 379990, 599990)


def test_pcfactory_api():
    data = {"content": {"pageable": {"totalPages": 2}, "items": [{"id": 58040, "nombre": "ZTE Neo", "marca": "ZTE",
            "stock": "+100", "slug": "58040-zte", "precio": {"efectivo": 329990, "normal": 340190, "referencia": 549990}}]}}
    r = parse("pcfactory", json.dumps(data))
    p = r.products[0]
    assert (p.price, p.list_price, p.in_stock, r.total_pages) == (329990, 549990, True, 2)
    assert p.url == "https://www.pcfactory.cl/producto/58040-zte"


def test_lider_search_result():
    nd = {"props": {"pageProps": {"initialData": {"searchResult": {"paginationV2": {"maxPage": 5}, "itemStacks": [{"items": [{
        "__typename": "Product", "id": "00880609587222", "name": "Smartphone Galaxy S25 Ultra 256GB.", "brand": "Samsung",
        "canonicalUrl": "/ip/x/00880609587222?athbdg=L1", "availabilityStatusV2": {"value": "IN_STOCK"},
        "priceInfo": {"linePrice": "$799.990", "wasPrice": "$1.469.990"}, "badges": {"flags": [{"text": "The New Black"}]}}]}]}}}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>'
    r = parse("lider", html)
    p = r.products[0]
    assert (p.price, p.list_price, p.in_stock, p.url, p.badges, r.total_pages) == (
        799990, 1469990, True, "https://www.lider.cl/ip/x/00880609587222", ["The New Black"], 5)


def test_abc_sfcc_tile():
    gtm = json.dumps({"ecommerce": {"impressions": {"name": "Xiaomi Redmi A7 64 GB", "brand": "XIAOMI"}}}).replace('"', "&quot;")
    html = (f'<div class="product-tile__wrapper" data-pid="29591210"><div data-gtm="{gtm}">'
            '<a class="image-link" href="/xiaomi/29591210.html"></a>'
            '<p class="la-polar price"><span class="price-value" data-value="99990.0">$99.990</span></p>'
            '<p class="internet price"><span class="price-value" data-value="109990.0">$109.990</span></p>'
            '<p class="normal price"><span class="price-value" data-value="199990.0">$199.990</span></p></div></div>')
    p = parse("abc", html, "https://www.abc.cl/tecnologia/").products[0]
    assert (p.name, p.price, p.card_price, p.list_price, p.url) == (
        "Xiaomi Redmi A7 64 GB", 109990, 99990, 199990, "https://www.abc.cl/xiaomi/29591210.html")


def test_hites_sfcc_tile():
    item = json.dumps({"item": {"item_name": "Honor Play 20A", "item_brand": "Honor", "price": 159990, "hites_price": 99990}}).replace('"', "&quot;")
    html = (f'<div class="plp-grid-tile" data-pid="980601001"><div data-gtmselectitem="{item}">'
            '<a class="image-item" href="/honor-980601001.html"></a> $99.990 $159.990 $169.990</div></div>')
    p = parse("hites", html, "https://www.hites.com/celulares/").products[0]
    assert (p.price, p.card_price, p.list_price) == (159990, 99990, 169990)


def test_entel_endeca():
    rec = {"@class": "com.endeca.infront.cartridge.model.Record", "attributes": {
        "productId": ["prod2790074"], "displayName": ["HONOR 600 Smart 256GB"], "priceFormatted": ["$379.990"],
        "referencePrice": ["479990.000000"], "inventoryStatus": ["IN_STOCK"], "installmentQty": ["24"],
        "showInstallmentQty": ["1"], "seoUrl": ["/personas/celulares/honor/prod2790074"], "brand": ["Honor"]}}
    html = "<script>var x = " + json.dumps({"records": [rec]}, indent=1) + "</script>"
    p = parse("entel", html).products[0]
    assert (p.price, p.list_price, p.installments, p.in_stock) == (379990, 479990, 24, True)
    assert p.url == "https://miportal.entel.cl/personas/celulares/honor/prod2790074"


def test_samsung_json_ld():
    ld = {"@type": "ItemList", "itemListElement": [{"item": {"@type": "Product", "name": "Galaxy S26 FE",
          "url": "https://www.samsung.com/cl/smartphones/galaxy-s/galaxy-s26-fe-pistachio-128gb-sm-s741blgjltl/",
          "offers": {"price": "839990", "availability": "https://schema.org/InStock"}}}]}
    p = parse("samsung", f'<script type="application/ld+json">{json.dumps(ld)}</script>').products[0]
    assert (p.name, p.price, p.in_stock) == ("Samsung Galaxy S26 FE 128GB", 839990, True)


def test_apple_cards():
    html = ('<a href="https://www.apple.com/cl/shop/buy-iphone/iphone-17" data-slot-name="s" data-display-name="x" '
            'data-part-number="IPHONE17_MAIN" > <div class="rf-hcard-content-title"> iPhone&nbsp;17 </div>'
            '<div class="rf-hcard-scrim-price price"> Desde <span class="nowrap">$999.990</span></div>')
    p = parse("apple", html).products[0]
    assert (p.key, p.name, p.price) == ("apple:IPHONE17_MAIN", "Apple iPhone 17 (desde)", 999990)


def test_product_page_json_ld():
    ld = {"@type": "Product", "name": "Moto G", "offers": {"price": "129990", "availability": "OutOfStock"}}
    p = parse("watch", f'<script type="application/ld+json">{json.dumps(ld)}</script>', "https://www.paris.cl/moto.html").products[0]
    assert (p.key, p.site, p.price, p.in_stock) == ("url:https://www.paris.cl/moto.html", "paris", 129990, False)
