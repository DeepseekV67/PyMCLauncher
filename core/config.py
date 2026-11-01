from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

from . import secure
from .util import read_json, write_json, ensure_dir

APP_DIR = Path(__file__).resolve().parent.parent
_DATA_ENV = os.environ.get("PYMCL_DATA_DIR")
DATA_DIR = Path(_DATA_ENV).resolve() if _DATA_ENV else APP_DIR
CONFIG_PATH = DATA_DIR / "config.json"
CACHE_DIR = DATA_DIR / "cache"
LOG_DIR = DATA_DIR / "logs"
RUNTIME_DIR = DATA_DIR / "runtime"

DEFAULT_CLIENT_ID = "00000000402b5328"

DEFAULTS = {
    "game_root": "",
    "source": "official",
    "memory_mb": 4096,
    "workers": 16,
    "java_path": "",
    "ms_client_id": DEFAULT_CLIENT_ID,
    "mod_api": "https://api.modrinth.com/v2",
    "open_log": True,
    "allow_newer_java": True,
    "download_assets": True,
    "auto_instance": True,
    "local_imported": False,
    "terracotta_name": "",
    "instances": [],
    "account": None,
    "account_protected": "",
    "accounts": [],
    "accounts_protected": "",
    "active_account": "",
}


def _version_count(root: Path) -> int:
    vdir = root / "versions"
    try:
        return sum(1 for p in vdir.iterdir() if p.is_dir())
    except OSError:
        return -1


def detect_game_root() -> Path:
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / ".minecraft")
    for drive in ("D", "C", "E", "F", "G"):
        candidates.append(Path(f"{drive}:/") / ".minecraft")
    candidates.append(Path.home() / ".minecraft")
    existing = [p for p in candidates if p.is_dir()]
    if existing:
        return max(existing, key=_version_count)
    if appdata:
        return Path(appdata) / ".minecraft"
    return Path.home() / ".minecraft"


def safe_folder_name(name: str, fallback: str = "instance") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:60] or fallback


def unique_dir(parent: Path, name: str) -> Path:
    base = safe_folder_name(name)
    candidate = parent / base
    i = 2
    while candidate.exists():
        candidate = parent / f"{base} ({i})"
        i += 1
    return candidate


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls) -> "Config":
        data = read_json(CONFIG_PATH)
        merged = dict(DEFAULTS)
        if isinstance(data, dict):
            merged.update(data)
        cfg = cls(merged)
        if cfg.data.get("ms_client_id") == "00000000-402b-4cd3-a82b-c45ab2f1d3f7":
            cfg.data["ms_client_id"] = DEFAULT_CLIENT_ID
            cfg.save()
        if not cfg.data.get("game_root"):
            cfg.data["game_root"] = str(detect_game_root())
            cfg.save()
        if (not cfg.data.get("accounts_protected")
                and not cfg.data.get("accounts")):
            legacy = cfg._legacy_account_data()
            if legacy:
                cfg.set_accounts_data(
                    [legacy], active=cfg.account_key(legacy), save=False)
                cfg.save()
        return cfg

    def save(self):
        write_json(CONFIG_PATH, self.data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value, save: bool = True):
        self.data[key] = value
        if save:
            self.save()

    @staticmethod
    def account_key(data: dict) -> str:
        if not isinstance(data, dict):
            return ""
        return f"{data.get('type') or 'offline'}:" \
               f"{data.get('uuid') or data.get('name') or ''}"

    def _legacy_account_data(self) -> dict | None:
        protected = self.data.get("account_protected")
        if protected and secure.available():
            try:
                data = json.loads(secure.unprotect_text(str(protected)))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        legacy = self.data.get("account")
        return legacy if isinstance(legacy, dict) else None

    def accounts_data(self) -> list[dict]:
        protected = self.data.get("accounts_protected")
        if protected and secure.available():
            try:
                data = json.loads(secure.unprotect_text(str(protected)))
                if isinstance(data, list):
                    return [dict(x) for x in data if isinstance(x, dict)]
            except Exception:
                pass
        plain = self.data.get("accounts")
        if isinstance(plain, list):
            result = [dict(x) for x in plain if isinstance(x, dict)]
            if result:
                return result
        legacy = self._legacy_account_data()
        return [legacy] if legacy else []

    def active_account_key(self) -> str:
        return str(self.data.get("active_account") or "")

    def account_data(self) -> dict | None:
        accounts = self.accounts_data()
        if not accounts:
            return None
        active = self.active_account_key()
        for data in accounts:
            if self.account_key(data) == active:
                return data
        return accounts[0]

    def set_accounts_data(self, accounts: list[dict], active: str = "",
                          save: bool = True):
        clean = [dict(x) for x in accounts
                 if isinstance(x, dict) and x.get("name")]
        self.data.pop("account", None)
        self.data.pop("account_protected", None)
        self.data.pop("accounts", None)
        self.data.pop("accounts_protected", None)
        self.data.pop("active_account", None)
        if clean:
            try:
                protected = secure.protect_text(
                    json.dumps(clean, ensure_ascii=False))
            except Exception:
                self.data["accounts"] = clean
            else:
                self.data["accounts_protected"] = protected
            key = active if any(self.account_key(x) == active for x in clean) \
                else self.account_key(clean[0])
            self.data["active_account"] = key
        if save:
            self.save()

    def set_account_data(self, data: dict | None, save: bool = True):
        if data:
            self.set_accounts_data([data], self.account_key(data), save=save)
        else:
            self.set_accounts_data([], save=save)

    def add_or_update_account(self, data: dict, save: bool = True):
        accounts = self.accounts_data()
        key = self.account_key(data)
        replaced = False
        for index, item in enumerate(accounts):
            if self.account_key(item) == key:
                accounts[index] = dict(data)
                replaced = True
                break
        if not replaced:
            accounts.append(dict(data))
        self.set_accounts_data(accounts, key, save=save)
        return key

    def set_active_account(self, key: str, save: bool = True):
        if not any(self.account_key(x) == key for x in self.accounts_data()):
            return False
        self.data["active_account"] = key
        if save:
            self.save()
        return True

    def remove_account(self, key: str, save: bool = True):
        accounts = [x for x in self.accounts_data()
                    if self.account_key(x) != key]
        self.set_accounts_data(accounts, save=save)
        return accounts

    def game_root(self) -> Path:
        p = Path(self.data.get("game_root") or detect_game_root())
        return p

    # ---------------- instances ----------------
    @property
    def instances(self) -> list[dict]:
        return self.data.setdefault("instances", [])

    def instance(self, inst_id: str) -> dict | None:
        for inst in self.instances:
            if inst.get("id") == inst_id:
                return inst
        return None

    def add_instance(self, name: str, version_id: str, game_dir: Path,
                     memory: int = 0, jvm_args: str = "", java_path: str = "",
                     loader: str = "vanilla", mc_version: str = "",
                     save: bool = True) -> dict:
        inst = {
            "id": uuid.uuid4().hex[:8],
            "name": name.strip() or version_id,
            "version": version_id,
            "mc_version": mc_version or version_id,
            "loader": loader,
            "game_dir": str(game_dir),
            "memory": int(memory or 0),
            "jvm_args": jvm_args or "",
            "java": java_path or "",
            "created": int(time.time()),
        }
        self.instances.append(inst)
        if save:
            self.save()
        return inst

    def update_instance(self, inst_id: str, **fields):
        inst = self.instance(inst_id)
        if not inst:
            return
        inst.update(fields)
        self.save()

    def remove_instance(self, inst_id: str):
        self.data["instances"] = [i for i in self.instances if i.get("id") != inst_id]
        self.save()

    # ---------------- paths ----------------
    @staticmethod
    def app_dir() -> Path:
        return APP_DIR

    @staticmethod
    def cache_dir() -> Path:
        return ensure_dir(CACHE_DIR)

    @staticmethod
    def log_dir() -> Path:
        return ensure_dir(LOG_DIR)

    @staticmethod
    def runtime_dir() -> Path:
        return ensure_dir(RUNTIME_DIR)
