import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pm-test-"))
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["SOLOTODO_CHECK"] = "0"           # los tests nunca consultan SoloTodo (red)
