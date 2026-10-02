"""Создаёт Google-документ в рабочей папке от имени пользователя."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from google_drive import GoogleDrive, GoogleDriveError


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    load_dotenv(Path(__file__).with_name(".env"))
    folder_id = os.getenv("FOLDER_ID", "").strip()
    if not folder_id:
        raise SystemExit("Укажите FOLDER_ID в файле .env.")

    drive = GoogleDrive(folder_id=folder_id)
    name = datetime.now().strftime("Документ %Y-%m-%d %H-%M")
    created = drive.create_document(name)
    print(f"Создан: {created.get('name')}")
    print(created.get("webViewLink") or created.get("id"))


if __name__ == "__main__":
    try:
        main()
    except GoogleDriveError as error:
        raise SystemExit(error) from error
