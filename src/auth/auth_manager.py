"""
In-App Authentication Manager for Warcraft III Lounge.
Supports username + password registration, login, and session persistence
designed for direct integration with free cloud backends (Supabase / PostgreSQL).
No third-party OAuth (Google, etc.) or email verification required.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from typing import Optional, Tuple
import urllib.error
import urllib.request

from ..core.config import logger

DEFAULT_SUPABASE_URL = "https://lzbkrtccsrhlhdldjxot.supabase.co"
DEFAULT_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imx6YmtydGNjc3JobGhkbGRqeG90Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTI2ODk5NzksImV4cCI6MjEwODI2NTk3OX0.JzGLG5oEPqviNhGQToDWeuRkTtIm_rZRQBQE78Q2-Nk"

@dataclass
class UserSession:
    username: str
    user_id: str
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    is_authenticated: bool = False
    created_at: float = 0.0
    expires_at: float = 0.0

class AuthManager:
    """
    Manages custom username/password accounts and session tokens.
    Connects to Supabase REST Auth API when configured, with seamless
    offline / local profile caching.
    """

    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None, config_dir: Optional[Path] = None):
        if supabase_url is not None:
            self.supabase_url = supabase_url.rstrip("/")
        else:
            self.supabase_url = os.environ.get("SUPABASE_URL", DEFAULT_SUPABASE_URL).rstrip("/")

        if supabase_key is not None:
            self.supabase_key = supabase_key
        else:
            self.supabase_key = os.environ.get("SUPABASE_KEY", DEFAULT_SUPABASE_KEY)

        profile = os.environ.get("W3L_PROFILE", "").strip()
        base_dir = Path(os.environ.get("APPDATA", ".")) / "Warcraft3Lounge"
        if config_dir is not None:
            self.config_dir = Path(config_dir)
        elif profile:
            self.config_dir = base_dir / f"profile_{profile}"
        else:
            self.config_dir = base_dir

        self.session_file = self.config_dir / "auth_session.json"
        self.accounts_file = self.config_dir / "local_accounts.json"
        self._ensure_config_dir()

        self.current_user: UserSession = self._load_saved_session()
        self._start_token_refresh_watcher()

    def _ensure_config_dir(self) -> None:
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _hash_password(self, password: str, salt: str = "W3LoungeSalt") -> str:
        return hashlib.sha256(f"{password}_{salt}".encode()).hexdigest()

    def _load_local_accounts(self) -> dict:
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_local_account(self, username: str, password_hash: str) -> None:
        accounts = self._load_local_accounts()
        accounts[username.lower()] = password_hash
        try:
            with open(self.accounts_file, "w", encoding="utf-8") as f:
                json.dump(accounts, f, indent=2)
        except Exception:
            pass

    def _load_saved_session(self) -> UserSession:
        """Loads cached session if user opted to stay logged in."""
        if self.session_file.exists():
            try:
                with open(self.session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    username = data.get("username", "Player")
                    user_id = data.get("user_id", "")
                    token = data.get("access_token")
                    refresh_token = data.get("refresh_token")
                    expires_at = data.get("expires_at", 0.0)
                    if username and user_id:
                        logger.info(f"Loaded cached session for user: '{username}'")
                        session = UserSession(
                            username=username,
                            user_id=user_id,
                            access_token=token,
                            refresh_token=refresh_token,
                            is_authenticated=True,
                            created_at=data.get("created_at", time.time()),
                            expires_at=expires_at
                        )
                        return session
            except Exception as e:
                logger.debug(f"Failed to load cached auth session: {e}")

        # If launched with an isolated profile, auto-initialize that profile user session
        profile = os.environ.get("W3L_PROFILE", "").strip()
        if profile:
            user = UserSession(
                username=profile,
                user_id=f"usr_{profile}",
                access_token=f"mock_token_{profile}",
                is_authenticated=True,
                created_at=time.time()
            )
            self._save_session(user)
            logger.info(f"Initialized isolated profile session for: '{profile}'")
            return user

        # Default unauthenticated guest profile
        return UserSession(username="Player", user_id="guest_001", is_authenticated=False)

    def _save_session(self, session: UserSession) -> None:
        try:
            with open(self.session_file, "w", encoding="utf-8") as f:
                json.dump(asdict(session), f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save auth session: {e}")

    def _clear_saved_session(self) -> None:
        if self.session_file.exists():
            try:
                self.session_file.unlink()
            except Exception:
                pass

    def register(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Registers a new user with username and password.
        Uses Supabase Cloud Database strictly.
        Does not automatically log in so user can verify login credentials.
        """
        username = username.strip()
        if len(username) < 3:
            return False, "Username must be at least 3 characters."
        if len(password) < 6:
            return False, "Password must be at least 6 characters."

        # Strictly authenticate via Supabase Cloud Database
        if self.supabase_url and self.supabase_key:
            return self._register_supabase(username, password)

        # Fallback ONLY when Supabase credentials are not configured (e.g. offline unit tests)
        accounts = self._load_local_accounts()
        if username.lower() in accounts:
            return False, "An account with that username already exists. Please log in."

        pw_hash = self._hash_password(password)
        self._save_local_account(username, pw_hash)
        logger.info(f"Registered local account for '{username}'")
        return True, f"Account '{username}' created successfully! Please log in."

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        """Logs in strictly via Supabase Cloud Database."""
        username = username.strip()
        if not username or not password:
            return False, "Please provide both username and password."

        # Strictly authenticate via Supabase Cloud Database
        if self.supabase_url and self.supabase_key:
            return self._login_supabase(username, password)

        # Fallback ONLY when Supabase credentials are not configured (e.g. offline unit tests)
        accounts = self._load_local_accounts()
        saved_hash = accounts.get(username.lower())
        pw_hash = self._hash_password(password)

        if saved_hash and saved_hash == pw_hash:
            user_id = f"usr_{hashlib.sha256(username.encode()).hexdigest()[:12]}"
            self.current_user = UserSession(
                username=username,
                user_id=user_id,
                access_token="local_token_" + user_id,
                is_authenticated=True,
                created_at=time.time()
            )
            self._save_session(self.current_user)
            return True, f"Welcome back, {username}!"

        return False, "Incorrect username or password."

    def logout(self) -> None:
        """Clears user session."""
        self.current_user = UserSession(username="Player", user_id="guest_001", is_authenticated=False)
        self._clear_saved_session()
        logger.info("User logged out.")

    def _register_supabase(self, username: str, password: str) -> Tuple[bool, str]:
        """Direct HTTPS call to Supabase Auth signup with username and password."""
        synthetic_email = f"{username.lower().strip()}@w3lounge.com"
        url = f"{self.supabase_url}/auth/v1/signup"
        payload = json.dumps({
            "email": synthetic_email,
            "password": password,
            "data": {
                "username": username,
                "display_name": username,
                "name": username,
                "full_name": username
            }
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}"
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                logger.info(f"Registered cloud account for '{username}' in Supabase.")
                return True, f"Account '{username}' created successfully! Please log in."
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"Supabase signup error: {err_body}")
            try:
                err_json = json.loads(err_body)
                msg = err_json.get("msg") or err_json.get("error_description") or "Registration failed."
                if "over_email_send_rate_limit" in err_body or "email rate limit" in err_body.lower():
                    msg = "Please turn OFF 'Confirm email' in Supabase -> Authentication -> Providers -> Email."
                elif "already registered" in msg.lower() or "already exists" in msg.lower():
                    msg = "That username is already taken. Please choose another or log in."
            except Exception:
                msg = f"HTTP Error {e.code}"
            return False, msg
        except Exception as e:
            err_str = str(e)
            if "11001" in err_str or "getaddrinfo failed" in err_str:
                return False, "Cannot reach login server. Check your internet connection or restore your Supabase project."
            return False, f"Network error connecting to auth server: {e}"

    def _login_supabase(self, username: str, password: str) -> Tuple[bool, str]:
        """Direct HTTPS call to Supabase Auth sign in with username and password."""
        synthetic_email = f"{username.lower().strip()}@w3lounge.com"
        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        payload = json.dumps({
            "email": synthetic_email,
            "password": password
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "apikey": self.supabase_key,
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                token = data.get("access_token")
                refresh_token = data.get("refresh_token")
                expires_in = data.get("expires_in", 3600)
                user_obj = data.get("user", {})
                user_id = user_obj.get("id", "")
                meta = user_obj.get("user_metadata", {})
                resolved_username = meta.get("display_name") or meta.get("username") or username

                self.current_user = UserSession(
                    username=resolved_username,
                    user_id=user_id,
                    access_token=token,
                    refresh_token=refresh_token,
                    is_authenticated=True,
                    created_at=time.time(),
                    expires_at=time.time() + float(expires_in)
                )
                self._save_session(self.current_user)
                return True, f"Welcome back, {resolved_username}!"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error(f"Supabase login error: {err_body}")
            if "email_not_confirmed" in err_body:
                return False, "Please turn OFF 'Confirm email' in Supabase: Auth -> Providers -> Email -> Save."
            if "invalid_credentials" in err_body or "invalid login credentials" in err_body.lower():
                return False, "Incorrect username or password."
            return False, "Invalid username or password."
        except Exception as e:
            err_str = str(e)
            if "11001" in err_str or "getaddrinfo failed" in err_str:
                return False, "Cannot reach login server. Check your internet connection or restore your Supabase project."
            return False, f"Network error during login: {e}"

    def refresh_session(self) -> bool:
        """Refreshes the Supabase access token using the stored refresh_token."""
        if not self.current_user.is_authenticated or not self.current_user.refresh_token:
            return False
        if not self.supabase_url or not self.supabase_key:
            return False

        url = f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token"
        payload = json.dumps({"refresh_token": self.current_user.refresh_token}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "apikey": self.supabase_key,
        }

        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=6.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                new_token = data.get("access_token")
                new_refresh = data.get("refresh_token")
                expires_in = data.get("expires_in", 3600)
                if new_token:
                    self.current_user.access_token = new_token
                    if new_refresh:
                        self.current_user.refresh_token = new_refresh
                    self.current_user.expires_at = time.time() + float(expires_in)
                    self._save_session(self.current_user)
                    logger.info("Successfully refreshed Supabase auth session token.")
                    return True
        except Exception as e:
            logger.debug(f"Failed to refresh auth session token: {e}")
        return False

    def _start_token_refresh_watcher(self) -> None:
        """Background thread checking if session needs periodic refresh."""
        def watcher():
            while True:
                time.sleep(300)  # Check every 5 minutes
                try:
                    if self.current_user.is_authenticated and self.current_user.refresh_token:
                        # If token expires within 10 minutes, refresh proactively
                        if self.current_user.expires_at and (self.current_user.expires_at - time.time() < 600):
                            self.refresh_session()
                except Exception:
                    pass

        t = threading.Thread(target=watcher, daemon=True, name="AuthTokenRefresher")
        t.start()
