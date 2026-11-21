"""Publica el sitio en la rama gh-pages (GitHub Pages, gratis) y restaura la BD desde allí si hace falta.

Cada publicación es un único commit huérfano forzado: la rama nunca crece (el historial vive en la BD).
"""
from __future__ import annotations

import gzip
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

import config

log = logging.getLogger("publisher")


def _git(*args: str, cwd: Path) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def publish(site_dir: Path = config.SITE_DIR) -> bool:
    if not (config.GH_TOKEN and config.GH_REPO):
        log.info("Publicación omitida: faltan GH_TOKEN / GH_REPO en .env")
        return False
    remote = f"https://x-access-token:{config.GH_TOKEN}@github.com/{config.GH_REPO}.git"
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "site"
        shutil.copytree(site_dir, work)
        _git("init", "-q", "-b", "gh-pages", cwd=work)
        _git("config", "user.name", "price-monitor-bot", cwd=work)
        _git("config", "user.email", "price-monitor-bot@users.noreply.github.com", cwd=work)
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", f"dashboard ({config.RUNNER_NAME})", cwd=work)
        try:
            _git("push", "-q", "--force", remote, "gh-pages", cwd=work)
        except RuntimeError as exc:   # nunca filtrar el token en logs
            raise RuntimeError(str(exc).replace(config.GH_TOKEN, "***")) from None
    log.info("Dashboard publicado en GitHub Pages (%s)", config.PUBLIC_SITE_URL or config.GH_REPO)
    return True


def restore_db_if_missing() -> bool:
    """Si la BD local no existe (VM nueva, runner de Actions), la baja del último respaldo publicado."""
    if config.DB_PATH.exists() or not config.PUBLIC_SITE_URL:
        return False
    url = f"{config.PUBLIC_SITE_URL}/data/prices.db.gz"
    try:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        if r.status_code != 200:
            log.info("Sin respaldo previo en %s (%s): se parte de cero", url, r.status_code)
            return False
        config.DB_PATH.write_bytes(gzip.decompress(r.content))
        log.info("BD restaurada desde %s (%.1f MB)", url, config.DB_PATH.stat().st_size / 1e6)
        return True
    except (httpx.HTTPError, OSError) as exc:
        log.warning("No se pudo restaurar la BD: %s", exc)
        return False
