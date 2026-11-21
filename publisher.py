"""Publica el sitio en la rama gh-pages (GitHub Pages, gratis) y restaura la BD desde allí si hace falta.

Cada publicación es un único commit huérfano forzado: la rama nunca crece (el historial vive en la BD).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

import config

log = logging.getLogger("publisher")


DEPLOY_KEY = config.DATA_DIR / "deploy_key"   # llave SSH solo-este-repo (la genera deploy/oracle/setup.sh)


def _git(*args: str, cwd: Path, env: dict | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=180, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _remote() -> tuple[str, dict | None] | None:
    """Token (GitHub Actions) o deploy key SSH (VM). Devuelve (url_remota, entorno_git)."""
    if not config.GH_REPO:
        return None
    if config.GH_TOKEN:
        return f"https://x-access-token:{config.GH_TOKEN}@github.com/{config.GH_REPO}.git", None
    if DEPLOY_KEY.exists():
        import os
        ssh = (f'ssh -i "{DEPLOY_KEY}" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new '
               f'-o UserKnownHostsFile="{config.DATA_DIR / "known_hosts"}"')
        return f"git@github.com:{config.GH_REPO}.git", {**os.environ, "GIT_SSH_COMMAND": ssh}
    return None


def publish(site_dir: Path = config.SITE_DIR) -> bool:
    target = _remote()
    if target is None:
        log.info("Publicación omitida: falta GH_REPO y una credencial (GH_TOKEN o data/deploy_key)")
        return False
    remote, env = target
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "site"
        shutil.copytree(site_dir, work)
        _git("init", "-q", "-b", "gh-pages", cwd=work)
        _git("config", "user.name", "price-monitor-bot", cwd=work)
        _git("config", "user.email", "price-monitor-bot@users.noreply.github.com", cwd=work)
        _git("add", "-A", cwd=work)
        _git("commit", "-q", "-m", f"dashboard ({config.RUNNER_NAME})", cwd=work)
        try:
            _git("push", "-q", "--force", remote, "gh-pages", cwd=work, env=env)
        except RuntimeError as exc:   # nunca filtrar el token en logs
            raise RuntimeError(str(exc).replace(config.GH_TOKEN, "***") if config.GH_TOKEN else str(exc)) from None
    log.info("Dashboard publicado en GitHub Pages (%s)", config.PUBLIC_SITE_URL or config.GH_REPO)
    return True


def restore_db_if_missing() -> bool:
    """Si la BD local no existe (VM nueva, runner de Actions), la baja del último respaldo publicado."""
    if config.DB_PATH.exists() or not config.PUBLIC_SITE_URL:
        return False
    from backup import ENC_NAME, PLAIN_NAME, decode
    try:
        for name, enc in ((ENC_NAME, True), (PLAIN_NAME, False)):
            url = f"{config.PUBLIC_SITE_URL}/data/{name}"
            r = httpx.get(url, timeout=60, follow_redirects=True)
            if r.status_code == 200:
                config.DB_PATH.write_bytes(decode(r.content, enc))
                log.info("BD restaurada desde %s (%.1f MB)", url, config.DB_PATH.stat().st_size / 1e6)
                return True
        log.info("Sin respaldo previo en %s: se parte de cero", config.PUBLIC_SITE_URL)
        return False
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        log.warning("No se pudo restaurar la BD: %s", exc)
        return False
