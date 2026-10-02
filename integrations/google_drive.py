"""Клиент Google Диска для подключения в другие приложения.

Пример:

    from google_drive import GoogleDrive

    drive = GoogleDrive(
        folder_id="id_рабочей_папки",
        credentials_path="путь/к/ключу.json",
    )
    files = drive.read()
    document = drive.create_document("Заметка")
    sheet = drive.create_spreadsheet("Клиенты")
    drive.delete(document["id"])
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from google.auth.exceptions import TransportError
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]
DEFAULT_CREDENTIALS = Path(__file__).with_name(
    "zerocoderlessons-480509-df826ffcdec9.json"
)
DEFAULT_TOKEN = Path(__file__).with_name("token.json")
GOOGLE_DOC = "application/vnd.google-apps.document"
GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"
FILE_FIELDS = "id, name, mimeType, modifiedTime, webViewLink, parents"


class GoogleDriveError(Exception):
    """Ошибка чтения или изменения файлов на Google Диске."""


def _prefer_ipv4() -> None:
    """httplib2 сначала выбирает IPv6, а он здесь не отвечает."""
    if getattr(socket.getaddrinfo, "_google_drive_ipv4", False):
        return

    original = socket.getaddrinfo

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        results = original(host, port, family, type, proto, flags)
        ipv4 = [item for item in results if item[0] == socket.AF_INET]
        return ipv4 or results

    getaddrinfo._google_drive_ipv4 = True
    socket.getaddrinfo = getaddrinfo


class GoogleDrive:
    """Создание, чтение и удаление файлов Google Диска через Drive API."""

    def __init__(
        self,
        folder_id: str | None = None,
        credentials_path: str | Path | None = None,
        client_secrets_path: str | Path | None = None,
        token_path: str | Path | None = None,
    ) -> None:
        credentials_file = Path(credentials_path or DEFAULT_CREDENTIALS)
        if not credentials_file.is_file():
            raise GoogleDriveError(f"JSON-ключ не найден: {credentials_file}")

        self.folder_id = str(folder_id).strip() if folder_id else None
        self._client_secrets_path = (
            Path(client_secrets_path) if client_secrets_path else None
        )
        self._token_path = Path(token_path) if token_path else DEFAULT_TOKEN
        self._user_files_api = None

        _prefer_ipv4()
        credentials = Credentials.from_service_account_file(
            str(credentials_file),
            scopes=SCOPES,
        )
        self._service = build(
            "drive",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )
        self._files = self._service.files()

    def read(self, folder_id: str | None = None) -> list[dict]:
        """Возвращает файлы рабочей папки. Без папки читает всё, что доступно аккаунту."""
        target = self._folder(folder_id)
        query = "trashed = false"
        if target:
            query = f"'{target}' in parents and trashed = false"

        files: list[dict] = []
        page_token = None
        while True:
            response = self._execute(
                self._files.list(
                    q=query,
                    pageSize=100,
                    pageToken=page_token,
                    fields=f"nextPageToken, files({FILE_FIELDS})",
                    orderBy="name",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files

    def create(
        self,
        name: str,
        mime_type: str = GOOGLE_DOC,
        folder_id: str | None = None,
    ) -> dict:
        """Создаёт Google-файл в рабочей папке и возвращает его данные."""
        if not name or not str(name).strip():
            raise GoogleDriveError("Не указано имя файла.")
        parent = self._folder(folder_id)
        if not parent:
            raise GoogleDriveError(
                "Не указана рабочая папка. "
                "Сервисный аккаунт создаёт файлы только в папке, открытой для него."
            )

        body = {
            "name": str(name).strip(),
            "mimeType": mime_type,
            "parents": [parent],
        }
        return self._execute(
            self._files.create(
                body=body,
                fields=FILE_FIELDS,
                supportsAllDrives=True,
            )
        )

    def create_document(self, name: str, folder_id: str | None = None) -> dict:
        """Создаёт Google-документ в папке от имени вошедшего пользователя."""
        return self._create_as_user(name, GOOGLE_DOC, folder_id)

    def create_spreadsheet(self, name: str, folder_id: str | None = None) -> dict:
        """Создаёт Google-таблицу в папке от имени вошедшего пользователя."""
        return self._create_as_user(name, GOOGLE_SHEET, folder_id)

    def delete(self, file_id: str) -> None:
        """Удаляет файл или папку по идентификатору."""
        if not file_id or not str(file_id).strip():
            raise GoogleDriveError("Не указан id файла.")
        self._execute(
            self._files.delete(
                fileId=str(file_id).strip(),
                supportsAllDrives=True,
            )
        )

    def parent_id(self, file_id: str) -> str | None:
        """Возвращает id папки, в которой лежит файл."""
        if not file_id or not str(file_id).strip():
            raise GoogleDriveError("Не указан id файла.")
        metadata = self._execute(
            self._files.get(
                fileId=str(file_id).strip(),
                fields="parents",
                supportsAllDrives=True,
            )
        )
        parents = metadata.get("parents") or []
        return parents[0] if parents else None

    def _create_as_user(
        self,
        name: str,
        mime_type: str,
        folder_id: str | None,
    ) -> dict:
        if not name or not str(name).strip():
            raise GoogleDriveError("Не указано имя файла.")
        parent = self._folder(folder_id)
        if not parent:
            raise GoogleDriveError("Не указана рабочая папка.")

        body = {
            "name": str(name).strip(),
            "mimeType": mime_type,
            "parents": [parent],
        }
        return self._execute(
            self._user_files().create(
                body=body,
                fields=FILE_FIELDS,
                supportsAllDrives=True,
            )
        )

    def _user_files(self):
        if self._user_files_api is None:
            service = build(
                "drive",
                "v3",
                credentials=self._user_credentials(),
                cache_discovery=False,
            )
            self._user_files_api = service.files()
        return self._user_files_api

    def _user_credentials(self):
        """Вход пользователя через OAuth2. Токен сохраняется и обновляется сам."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials as UserCredentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        token_file = self._token_path
        credentials = None
        if token_file.is_file():
            credentials = UserCredentials.from_authorized_user_file(
                str(token_file),
                SCOPES,
            )

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        elif not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._client_secrets_file()),
                SCOPES,
            )
            credentials = flow.run_local_server(port=0)
            token_file.write_text(credentials.to_json(), encoding="utf-8")
            return credentials

        token_file.write_text(credentials.to_json(), encoding="utf-8")
        return credentials

    def _client_secrets_file(self) -> Path:
        if self._client_secrets_path:
            secrets = self._client_secrets_path
            if not secrets.is_file():
                raise GoogleDriveError(f"OAuth-клиент не найден: {secrets}")
            return secrets

        matches = sorted(Path(__file__).parent.glob("client_secret*.json"))
        if not matches:
            raise GoogleDriveError(
                "В папке проекта нет JSON-файла OAuth-клиента client_secret*.json."
            )
        return matches[0]

    def _folder(self, folder_id: str | None) -> str | None:
        target = folder_id if folder_id is not None else self.folder_id
        if not target:
            return None
        target = str(target).strip()
        return target or None

    @staticmethod
    def _execute(request):
        try:
            return request.execute()
        except HttpError as error:
            details = getattr(error, "reason", None) or str(error)
            raise GoogleDriveError(details) from error
        except (TimeoutError, OSError, TransportError) as error:
            raise GoogleDriveError(
                "Нет соединения с Google API. Проверьте интернет или VPN."
            ) from error


def _mime_label(mime_type: str) -> str:
    labels = {
        GOOGLE_FOLDER: "Папка",
        GOOGLE_SHEET: "Таблица",
        GOOGLE_DOC: "Документ",
        "application/vnd.google-apps.presentation": "Презентация",
    }
    return labels.get(mime_type, mime_type)


def _print_files(files: list[dict]) -> None:
    if not files:
        print("В папке нет файлов.")
        return

    print(f"Файлов: {len(files)}")
    for index, item in enumerate(files, start=1):
        kind = _mime_label(item.get("mimeType", ""))
        print(f"{index}: [{kind}] {item.get('name')} | id={item.get('id')}")


def main() -> None:
    import sys

    from dotenv import load_dotenv

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(Path(__file__).with_name(".env"))
    folder_id = os.getenv("FOLDER_ID", "").strip() or None
    drive = GoogleDrive(folder_id=folder_id)

    if not drive.folder_id:
        spreadsheet_id = os.getenv("SPREADSHEET_ID", "").strip()
        if spreadsheet_id:
            drive.folder_id = drive.parent_id(spreadsheet_id)

    if drive.folder_id:
        print(f"Рабочая папка: {drive.folder_id}")
    else:
        print("Рабочая папка не задана. Показаны все файлы, доступные сервисному аккаунту.")

    try:
        files = drive.read()
    except GoogleDriveError:
        if not folder_id and drive.folder_id:
            print("Папку таблицы прочитать не удалось. Показаны все доступные файлы.")
            drive.folder_id = None
            files = drive.read()
        else:
            raise

    _print_files(files)
    if drive.folder_id:
        return

    for item in files:
        if item.get("mimeType") != GOOGLE_FOLDER:
            continue
        print(f"\nСодержимое папки «{item.get('name')}»:")
        _print_files(drive.read(item.get("id")))


if __name__ == "__main__":
    main()
