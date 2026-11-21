"""🔐 Respaldo de la base de datos entre ventanas de GitHub Actions.

El respaldo vive en la rama pública gh-pages, así que se CIFRA (Fernet/AES) con la clave DB_KEY guardada en
GitHub Secrets: contiene tus objetivos, productos vigilados y el chat de Telegram. Sin DB_KEY (uso local) se
guarda sin cifrar, como antes.

    python backup.py restore data/prices.db        # desde la rama gh-pages (lo usan los workflows)
"""
from __future__ import annotations

import gzip
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ENC_NAME, PLAIN_NAME = "prices.db.enc", "prices.db.gz"


def _key() -> bytes | None:
    k = os.getenv("DB_KEY", "").strip()
    return k.encode() if k else None


def snapshot_bytes(db_path: Path) -> bytes:
    """Copia consistente (API de backup de sqlite) comprimida con gzip."""
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "snap.db"
        src, dst = sqlite3.connect(db_path), sqlite3.connect(snap)
        src.backup(dst)
        dst.close()
        src.close()
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9) as gz:
            gz.write(snap.read_bytes())
        return buf.getvalue()


def write_backup(db_path: Path, data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    payload, key = snapshot_bytes(db_path), _key()
    for old in (ENC_NAME, PLAIN_NAME):                  # nunca dejar las dos versiones
        (data_dir / old).unlink(missing_ok=True)
    if key:
        from cryptography.fernet import Fernet
        out = data_dir / ENC_NAME
        out.write_bytes(Fernet(key).encrypt(payload))
    else:
        out = data_dir / PLAIN_NAME
        out.write_bytes(payload)
    return out


def decode(blob: bytes, encrypted: bool) -> bytes:
    if encrypted:
        key = _key()
        if not key:
            raise RuntimeError("El respaldo está cifrado y falta DB_KEY")
        from cryptography.fernet import Fernet
        blob = Fernet(key).decrypt(blob)
    return gzip.decompress(blob)


def _git_show(path: str) -> bytes | None:
    r = subprocess.run(["git", "show", f"origin/gh-pages:{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 and r.stdout else None


def restore_from_git(dest: Path) -> str:
    """Restaura desde la rama gh-pages: primero el cifrado; si no existe, el antiguo sin cifrar (migración)."""
    subprocess.run(["git", "fetch", "-q", "--depth", "1", "origin", "gh-pages"], capture_output=True)
    for name, enc in ((ENC_NAME, True), (PLAIN_NAME, False)):
        blob = _git_show(f"data/{name}")
        if blob:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(decode(blob, enc))
            return f"restaurada desde {name} ({dest.stat().st_size / 1e6:.1f} MB)"
    return "sin respaldo previo: se parte de cero"


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "restore":
        print(restore_from_git(Path(sys.argv[2])))
    else:
        print(__doc__)
        sys.exit(2)
