"""Google Drive API v3 client with OAuth 2.0 loopback, keyring persistence, and resumable chunked uploads."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
import requests

try:
    import keyring
except ImportError:
    keyring = None

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from cookeddisk.config import AppConfig, get_default_app_dir
from cookeddisk.logger import get_logger

logger = get_logger("drive_client")

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/userinfo.email",
]

KEYRING_SERVICE = "CookedDisk"
KEYRING_REFRESH_TOKEN_KEY = "google_drive_refresh_token"
KEYRING_USER_EMAIL_KEY = "google_user_email"

# Embedded default desktop OAuth client credentials (can be overridden by user)
DEFAULT_CLIENT_CONFIG = {
    "installed": {
        "client_id": os.environ.get(
            "COOKEDDISK_CLIENT_ID",
            "108422749504-defaultclientidcookeddiskplaceholder.apps.googleusercontent.com",
        ),
        "project_id": "cookeddisk-app",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": os.environ.get("COOKEDDISK_CLIENT_SECRET", "placeholder_secret"),
        "redirect_uris": ["http://localhost"],
    }
}


@dataclass
class UploadResult:
    file_id: str
    name: str
    size: int
    md5_checksum: Optional[str]


class DriveAuthManager:
    """Manages OAuth 2.0 desktop authentication and keyring persistence."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        self.credentials: Optional[Credentials] = None

    def get_oauth_client_config(self) -> Dict[str, Any]:
        """Look for client_secret.json in app dir or workspace, fallback to embedded."""
        paths_to_check = [
            get_default_app_dir() / "client_secret.json",
            Path.cwd() / "client_secret.json",
        ]
        for p in paths_to_check:
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read custom client_secret.json at {p}: {e}")
        return DEFAULT_CLIENT_CONFIG

    def get_stored_refresh_token(self) -> Optional[str]:
        if keyring:
            try:
                token = keyring.get_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN_KEY)
                if token:
                    return token
            except Exception as e:
                logger.warning(f"Could not read from keyring: {e}")

        # Fallback to local encrypted/hidden token file if keyring fails on some Windows setups
        token_file = get_default_app_dir() / ".token_fallback"
        if token_file.is_file():
            try:
                return token_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return None

    def store_refresh_token(self, refresh_token: str, user_email: Optional[str] = None) -> None:
        saved_in_keyring = False
        if keyring:
            try:
                keyring.set_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN_KEY, refresh_token)
                if user_email:
                    keyring.set_password(KEYRING_SERVICE, KEYRING_USER_EMAIL_KEY, user_email)
                saved_in_keyring = True
            except Exception as e:
                logger.warning(f"Could not write refresh token to keyring: {e}")

        if not saved_in_keyring:
            token_file = get_default_app_dir() / ".token_fallback"
            try:
                token_file.write_text(refresh_token, encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to write fallback token file: {e}")

        if user_email:
            email_file = get_default_app_dir() / ".user_email"
            try:
                email_file.write_text(user_email, encoding="utf-8")
            except Exception:
                pass

    def get_connected_user(self) -> Optional[str]:
        if keyring:
            try:
                email = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_EMAIL_KEY)
                if email:
                    return email
            except Exception:
                pass

        email_file = get_default_app_dir() / ".user_email"
        if email_file.is_file():
            try:
                return email_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        return None

    def sign_out(self) -> None:
        """Clear tokens and disconnect."""
        self.credentials = None
        if keyring:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_REFRESH_TOKEN_KEY)
            except Exception:
                pass
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER_EMAIL_KEY)
            except Exception:
                pass

        token_file = get_default_app_dir() / ".token_fallback"
        if token_file.exists():
            try:
                token_file.unlink()
            except Exception:
                pass

        email_file = get_default_app_dir() / ".user_email"
        if email_file.exists():
            try:
                email_file.unlink()
            except Exception:
                pass
        logger.info("Signed out from Google Drive.")

    def get_valid_credentials(self) -> Optional[Credentials]:
        """Attempt silent refresh from stored refresh token."""
        if self.credentials and self.credentials.valid:
            return self.credentials

        refresh_token = self.get_stored_refresh_token()
        if not refresh_token:
            return None

        cfg = self.get_oauth_client_config()
        client_info = cfg.get("installed", cfg.get("web", {}))
        client_id = client_info.get("client_id")
        client_secret = client_info.get("client_secret")
        token_uri = client_info.get("token_uri", "https://oauth2.googleapis.com/token")

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=token_uri,
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )

        try:
            creds.refresh(Request())
            self.credentials = creds
            logger.info("Successfully refreshed Google OAuth token.")
            return creds
        except Exception as e:
            logger.error(f"Silent refresh of Google token failed: {e}")
            return None

    def run_interactive_login(
        self, custom_client_config: Optional[Dict[str, Any]] = None
    ) -> Credentials:
        """Run loopback OAuth desktop flow."""
        cfg = custom_client_config or self.get_oauth_client_config()
        flow = InstalledAppFlow.from_client_config(cfg, scopes=SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")

        # Fetch user email for display
        user_email = None
        try:
            oauth2_service = build("oauth2", "v2", credentials=creds)
            user_info = oauth2_service.userinfo().get().execute()
            user_email = user_info.get("email")
        except Exception as e:
            logger.warning(f"Could not fetch userinfo email: {e}")

        if creds.refresh_token:
            self.store_refresh_token(creds.refresh_token, user_email)

        self.credentials = creds
        logger.info(f"Signed in successfully as {user_email or 'Google User'}.")
        return creds


class DriveClient:
    """Handles communication with Google Drive v3 API and chunked resumable uploads."""

    def __init__(self, auth_manager: Optional[DriveAuthManager] = None, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.load()
        self.auth_manager = auth_manager or DriveAuthManager(self.config)
        self._service = None
        self._base_folder_id: Optional[str] = None
        self._session_folder_cache: Dict[str, str] = {}

    def get_service(self):
        creds = self.auth_manager.get_valid_credentials()
        if not creds:
            raise RuntimeError("Not signed in to Google Drive. Please authenticate first.")
        if not self._service:
            self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def get_or_create_base_folder(self) -> str:
        if self._base_folder_id:
            return self._base_folder_id

        service = self.get_service()
        folder_name = self.config.drive_base_folder_name
        query = (
            f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false and 'root' in parents"
        )
        res = service.files().list(q=query, fields="files(id, name)", spaces="drive").execute()
        files = res.get("files", [])
        if files:
            self._base_folder_id = files[0]["id"]
            return self._base_folder_id

        # Create base folder
        metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["root"],
        }
        folder = service.files().create(body=metadata, fields="id").execute()
        self._base_folder_id = folder["id"]
        logger.info(f"Created Drive base folder '{folder_name}' id: {self._base_folder_id}")
        return self._base_folder_id

    def get_or_create_session_folder(self, session_name: str) -> str:
        if session_name in self._session_folder_cache:
            return self._session_folder_cache[session_name]

        service = self.get_service()
        base_id = self.get_or_create_base_folder()
        clean_name = f"CookedDisk / {session_name}"

        query = (
            f"name = '{clean_name}' and mimeType = 'application/vnd.google-apps.folder' "
            f"and trashed = false and '{base_id}' in parents"
        )
        res = service.files().list(q=query, fields="files(id, name)", spaces="drive").execute()
        files = res.get("files", [])
        if files:
            folder_id = files[0]["id"]
            self._session_folder_cache[session_name] = folder_id
            return folder_id

        metadata = {
            "name": clean_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [base_id],
        }
        folder = service.files().create(body=metadata, fields="id").execute()
        folder_id = folder["id"]
        self._session_folder_cache[session_name] = folder_id
        logger.info(f"Created session folder '{clean_name}' id: {folder_id}")
        return folder_id

    def get_file_metadata(self, file_id: str) -> Dict[str, Any]:
        """Fetch remote file size and md5Checksum from Google Drive."""
        service = self.get_service()
        return (
            service.files()
            .get(fileId=file_id, fields="id, name, size, md5Checksum, trashed")
            .execute()
        )

    def _execute_with_backoff(
        self,
        func: Callable[[], Any],
        max_retries: Optional[int] = None,
        is_cancel_requested: Optional[Callable[[], bool]] = None,
    ) -> Any:
        """Executes a function with exponential backoff on 403, 429, 5xx, or network errors."""
        retries = max_retries or self.config.max_upload_retries
        base_delay = self.config.retry_base_delay_sec
        max_delay = self.config.retry_max_delay_sec

        for attempt in range(retries):
            if is_cancel_requested and is_cancel_requested():
                raise InterruptedError("Operation was canceled.")

            try:
                return func()
            except HttpError as http_err:
                status = http_err.resp.status
                if status in (403, 429, 500, 502, 503, 504) and attempt < retries - 1:
                    sleep_time = min(max_delay, base_delay * (2**attempt)) + random.uniform(0.1, 0.9)
                    logger.warning(
                        f"HTTP {status} from Drive API (attempt {attempt + 1}/{retries}). Retrying in {sleep_time:.1f}s: {http_err}"
                    )
                    time.sleep(sleep_time)
                else:
                    raise
            except (requests.RequestException, OSError, ConnectionError) as net_err:
                if attempt < retries - 1:
                    sleep_time = min(max_delay, base_delay * (2**attempt)) + random.uniform(0.1, 0.9)
                    logger.warning(
                        f"Network error (attempt {attempt + 1}/{retries}). Retrying in {sleep_time:.1f}s: {net_err}"
                    )
                    time.sleep(sleep_time)
                else:
                    raise

    def initiate_resumable_upload(
        self,
        file_name: str,
        total_size: int,
        parent_folder_id: str,
        mime_type: str = "video/mp4",
    ) -> str:
        """Initiate resumable upload session and return the upload URL."""
        creds = self.auth_manager.get_valid_credentials()
        if not creds:
            raise RuntimeError("Credentials not available for upload.")

        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
        headers = {
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": mime_type,
            "X-Upload-Content-Length": str(total_size),
        }
        body = {
            "name": file_name,
            "parents": [parent_folder_id],
        }

        def _do_init():
            nonlocal creds
            if not creds.valid:
                creds.refresh(Request())
                headers["Authorization"] = f"Bearer {creds.token}"
            resp = requests.post(url, headers=headers, json=body, timeout=30)
            if resp.status_code == 401:
                # Token expired, refresh and retry
                creds.refresh(Request())
                headers["Authorization"] = f"Bearer {creds.token}"
                resp = requests.post(url, headers=headers, json=body, timeout=30)
            resp.raise_for_status()
            upload_uri = resp.headers.get("Location")
            if not upload_uri:
                raise RuntimeError("Google Drive did not return a Location header for resumable upload.")
            return upload_uri

        return self._execute_with_backoff(_do_init)

    def query_resumable_status(self, upload_uri: str, total_size: int) -> int:
        """Query how many bytes have been received by Google Drive for this upload session."""
        headers = {
            "Content-Range": f"bytes */{total_size}",
        }
        try:
            resp = requests.put(upload_uri, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                return total_size
            if resp.status_code == 308:
                range_header = resp.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    # Format: bytes=0-12345
                    last_byte = int(range_header.split("-")[1])
                    return last_byte + 1
            return 0
        except Exception as e:
            logger.warning(f"Failed to query resumable status: {e}")
            return 0

    def upload_file_resumable(
        self,
        file_path: Path | str,
        parent_folder_id: str,
        resumable_uri: Optional[str] = None,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        is_cancel_requested: Optional[Callable[[], bool]] = None,
    ) -> Tuple[UploadResult, str]:
        """
        Perform chunked resumable upload (8-32 MB chunks).
        Returns (UploadResult, resumable_uri).
        """
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Segment file not found: {path}")

        total_size = path.stat().st_size
        chunk_size = self.config.chunk_size_bytes
        # Ensure chunk size is a multiple of 256 KiB
        chunk_size = (chunk_size // (256 * 1024)) * (256 * 1024)
        if chunk_size <= 0:
            chunk_size = 8 * 1024 * 1024

        creds = self.auth_manager.get_valid_credentials()
        if not creds:
            raise RuntimeError("Authentication required for upload.")

        # Determine MIME type
        ext = path.suffix.lower()
        mime_type = "video/x-matroska" if ext == ".mkv" else "video/mp4"

        # Resume or initiate upload session
        active_uri = resumable_uri
        start_byte = 0

        if active_uri:
            start_byte = self.query_resumable_status(active_uri, total_size)
            if start_byte >= total_size:
                logger.info(f"File {path.name} was already fully received by Drive.")
            else:
                logger.info(f"Resuming upload of {path.name} from byte {start_byte}/{total_size}")
        else:
            active_uri = self.initiate_resumable_upload(
                path.name, total_size, parent_folder_id, mime_type
            )
            start_byte = 0

        current_byte = start_byte
        last_time = time.time()
        last_bytes = current_byte

        with open(path, "rb") as f:
            f.seek(current_byte)

            while current_byte < total_size:
                if is_cancel_requested and is_cancel_requested():
                    raise InterruptedError("Upload canceled by user.")

                to_read = min(chunk_size, total_size - current_byte)
                chunk_data = f.read(to_read)
                if not chunk_data:
                    break

                end_byte = current_byte + len(chunk_data) - 1
                headers = {
                    "Content-Range": f"bytes {current_byte}-{end_byte}/{total_size}",
                    "Content-Length": str(len(chunk_data)),
                }

                def _send_chunk():
                    nonlocal creds
                    if not creds.valid:
                        creds.refresh(Request())
                    return requests.put(
                        active_uri,
                        headers=headers,
                        data=chunk_data,
                        timeout=90,
                    )

                chunk_start = time.time()
                response = self._execute_with_backoff(
                    _send_chunk, is_cancel_requested=is_cancel_requested
                )
                chunk_elapsed = time.time() - chunk_start

                # Bandwidth Limiter (Ping Protection)
                if self.config.max_upload_kbps > 0 and len(chunk_data) > 0:
                    target_seconds = (len(chunk_data) * 8.0) / (self.config.max_upload_kbps * 1000.0)
                    throttle_delay = target_seconds - chunk_elapsed
                    if throttle_delay > 0:
                        sleep_end = time.time() + throttle_delay
                        while time.time() < sleep_end:
                            if is_cancel_requested and is_cancel_requested():
                                raise InterruptedError("Upload canceled during bandwidth throttling.")
                            time.sleep(min(0.05, sleep_end - time.time()))

                if response.status_code in (200, 201):
                    # Upload finished!
                    data = response.json()
                    file_id = data.get("id")
                    name = data.get("name", path.name)
                    # Fetch full metadata to guarantee md5Checksum and exact size
                    meta = self.get_file_metadata(file_id)
                    drive_size = int(meta.get("size", total_size))
                    drive_md5 = meta.get("md5Checksum")

                    if on_progress:
                        on_progress(total_size, total_size, 0.0)

                    return UploadResult(
                        file_id=file_id,
                        name=name,
                        size=drive_size,
                        md5_checksum=drive_md5,
                    ), active_uri

                elif response.status_code == 308:
                    # Incomplete, chunk succeeded
                    current_byte = end_byte + 1
                    now = time.time()
                    elapsed = now - last_time
                    speed = ((current_byte - last_bytes) / elapsed) if elapsed > 0 else 0.0
                    last_time = now
                    last_bytes = current_byte

                    if on_progress:
                        on_progress(current_byte, total_size, speed)
                else:
                    raise RuntimeError(
                        f"Unexpected response code during chunk upload: {response.status_code} - {response.text}"
                    )

        # In case it reached total_size
        meta = self.query_resumable_status(active_uri, total_size)
        raise RuntimeError("Upload loop terminated without 200/201 response from Google Drive.")

