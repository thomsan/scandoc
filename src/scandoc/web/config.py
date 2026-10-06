from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from cryptography.fernet import Fernet
import json
import ssl


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCANDOC_")
    data_dir: Path = Path.home() / ".local/share/scandoc"
    hosted: bool = False
    origin: str = "http://127.0.0.1:8000"
    paperless_url: str = ""
    paperless_public_url: str = ""
    encryption_key: str = ""
    company_group_id: int = 0
    config_file: Path | None = None
    ca_file: str | None = None
    max_pages: int = 20
    max_image_bytes: int = 25 * 1024 * 1024
    max_document_bytes: int = 200 * 1024 * 1024
    max_pixels: int = 24_000_000
    worker_enabled: bool = True
    session_hours: int = 24

    def prepare(self):
        if self.hosted and (not self.paperless_url or not self.encryption_key or not self.origin.startswith("https://")):
            raise ValueError("Hosted mode requires Paperless, an encryption key and an HTTPS origin")
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.encryption_key:
            path = self.data_dir / "encryption.key"
            if not path.exists():
                with path.open("xb") as file:
                    file.write(Fernet.generate_key())
                path.chmod(0o600)
            self.encryption_key = path.read_text().strip()
        Fernet(self.encryption_key.encode())
        if self.ca_file:
            try:
                ssl.create_default_context(cafile=self.ca_file)
            except (OSError, ssl.SSLError) as exc:
                raise ValueError("The configured public CA certificate must be readable and valid") from exc

    def configured_destinations(self):
        result = {"download": {"id": "download", "name": "Download to device", "kind": "download", "readonly": True}}
        if self.paperless_url:
            result["paperless"] = {"id": "paperless", "name": "Paperless", "kind": "paperless", "readonly": True}
        if self.config_file:
            for destination in json.loads(self.config_file.read_text()).get("destinations", []):
                result[destination["id"]] = {**destination, "readonly": True}
        return result
