"""🔐 Privacidad con repo público: respaldo cifrado, feed público sin datos personales, sin indexar."""
import gzip
import sqlite3

import pytest

import backup
import config
from models import Product
from storage import PriceStore


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "p.db")
    with PriceStore(config.DB_PATH) as s:
        s.set_meta("tg:chat", "123456789")
        s.set_meta("tg:targets", [{"label": "S25 Ultra bajo $900.000", "target_price": 900000, "keywords": ["s25"]}])
        s.observe(Product("abc:1", "abc", "Smart TV 55", "https://a.cl/1", 300000), "g", "src", "2026-11-21T10:00:00",
                  "televisores")
        s.observe(Product("url:https://f.cl/mi-producto", "falabella", "Mi producto vigilado", "https://f.cl/mi-producto",
                          500000), "g", "watch", "2026-11-21T10:00:00")
        s.add_alert("abc:1", "drop", 300000, "📉 Bajó: Smart TV 55", "2026-11-21T10:00:00")
        s.add_alert("abc:1", "target", 300000, "🎯 Objetivo: S25 Ultra bajo $900.000", "2026-11-21T10:00:00")
        s.commit()
    return config.DB_PATH


def test_backup_is_encrypted_and_roundtrips(db, tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("DB_KEY", Fernet.generate_key().decode())
    out = backup.write_backup(db, tmp_path / "site" / "data")
    blob = out.read_bytes()
    assert out.name == "prices.db.enc" and not (tmp_path / "site" / "data" / "prices.db.gz").exists()
    assert b"123456789" not in blob and b"S25 Ultra" not in blob            # ilegible sin la clave
    with pytest.raises(Exception):
        gzip.decompress(blob)
    restored = tmp_path / "r.db"
    restored.write_bytes(backup.decode(blob, encrypted=True))
    assert sqlite3.connect(restored).execute("SELECT v FROM meta WHERE k='tg:chat'").fetchone()[0] == '"123456789"'
    monkeypatch.setenv("DB_KEY", Fernet.generate_key().decode())           # otra clave: no descifra
    with pytest.raises(Exception):
        backup.decode(blob, encrypted=True)


def test_without_key_local_backup_stays_plain(db, tmp_path, monkeypatch):
    monkeypatch.delenv("DB_KEY", raising=False)
    out = backup.write_backup(db, tmp_path / "data")
    assert out.name == "prices.db.gz" and gzip.decompress(out.read_bytes())[:15] == b"SQLite format 3"


def test_public_data_has_no_personal_info(db):
    from dashboard import build_data
    with PriceStore(db) as s:
        d = build_data(s)
    assert [p["key"] for p in d["products"]] == ["abc:1"]                   # /seguir no se publica
    assert [a["kind"] for a in d["alerts"]] == ["drop"]                      # objetivos fuera del feed
    text = str(d)
    assert "123456789" not in text and "S25 Ultra" not in text


def test_site_is_not_indexable(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SITE_DIR", tmp_path / "site")
    from dashboard import build_site
    out = build_site(tmp_path / "site")
    assert (out / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"
    assert 'name="robots" content="noindex' in (out / "index.html").read_text(encoding="utf-8")
