"""Local Google Ads credentials."""
import json
import os
import threading
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPE = "https://www.googleapis.com/auth/adwords"
_folder = None
_lock = threading.Lock()

def connection_folder():
    return Path(os.environ.get("ADCHESTRA_LOCAL_HOME", str(Path.home() / ".adchestra-local"))).expanduser().resolve()

def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=2)
    temporary.chmod(0o600)
    temporary.replace(path)

def set_connection_folder(folder):
    global _folder
    _folder = folder

def get_ads_credentials(config):
    folder = _folder or connection_folder()
    with _lock:
        path = folder / "token.json"
        if not path.exists():
            raise RuntimeError("Run adchestra-local setup first.")
        credentials = Credentials.from_authorized_user_file(str(path), [SCOPE])
        if not credentials.valid:
            try:
                credentials.refresh(Request())
            except Exception:
                raise RuntimeError("Google authorization expired. Run adchestra-local reconnect.") from None
            private_json(path, json.loads(credentials.to_json()))
        return credentials
