"""Google Drive からお弁当写真を取得する（サービスアカウント・読み取り専用）。"""
from __future__ import annotations

import io
import json

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from common import env

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp")


def service():
    info = json.loads(env("GOOGLE_SERVICE_ACCOUNT_JSON", required=True))
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list(svc, q: str, fields: str) -> list[dict]:
    files, token = [], None
    while True:
        res = svc.files().list(
            q=q, fields=f"nextPageToken, files({fields})", pageToken=token,
            orderBy="createdTime", pageSize=100,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        files += res.get("files", [])
        token = res.get("nextPageToken")
        if not token:
            return files


def list_images(svc, folder_id: str) -> list[dict]:
    """フォルダ内の画像を古い順に返す（HEIC は mimeType が付かないことがあるので拡張子でも判定）。"""
    q = f"'{folder_id}' in parents and trashed=false"
    files = _list(svc, q, "id,name,mimeType,createdTime,description")
    return [f for f in files
            if f["mimeType"].startswith("image/") or f["name"].lower().endswith(IMAGE_EXT)]


def find_memo(svc, folder_id: str, image: dict) -> str:
    """写真の「説明」欄、または同名の .txt（例: IMG_0001.txt）をメモとして読む。"""
    memo = (image.get("description") or "").strip()
    stem = image["name"].rsplit(".", 1)[0].replace("'", "\\'")
    q = f"'{folder_id}' in parents and trashed=false and name = '{stem}.txt'"
    for f in _list(svc, q, "id,name"):
        memo = (memo + "\n" + download(svc, f["id"]).decode("utf-8", "ignore")).strip()
    return memo


def download(svc, file_id: str) -> bytes:
    buf = io.BytesIO()
    req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue()
