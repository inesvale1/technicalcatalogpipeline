from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus


def build_database_engine(settings: "DatabaseConnectionSettings"):
    """Create a SQLAlchemy engine passing credentials via connect_args.

    Using connect_args instead of embedding credentials in the URL avoids
    quote_plus encoding issues with domain usernames that contain backslashes
    (e.g. SEFAZ2\\49756615), which oracledb does not decode correctly from URLs.
    """
    try:
        from sqlalchemy import create_engine
    except ImportError as exc:
        raise RuntimeError(
            "Database access requires SQLAlchemy. Install it with: pip install sqlalchemy"
        ) from exc

    password = _read_keyring_password(settings)

    if settings.connection_uri:
        uri = _inject_password(str(settings.connection_uri).strip(), password)
        return create_engine(uri)

    driver = str(settings.driver_class_name or "oracle+oracledb").strip()
    username = _require(settings.username, "db_username")
    if password is None:
        raise ValueError("A keyring password is required when db_connection_uri is not provided.")

    connect_args: dict = {"user": username, "password": password}

    if settings.dsn:
        connect_args["dsn"] = str(settings.dsn)
    else:
        host = _require(settings.host, "db_host")
        connect_args["host"] = host
        connect_args["port"] = int(settings.port or 1521)
        if settings.service_name:
            connect_args["service_name"] = str(settings.service_name)
        elif settings.sid:
            connect_args["sid"] = str(settings.sid)
        else:
            raise ValueError("Configure db_service_name, db_sid, or db_dsn for Oracle access.")

    return create_engine(f"{driver}://", connect_args=connect_args)


@dataclass(frozen=True)
class DatabaseConnectionSettings:
    connection_uri: str | None = None
    driver_class_name: str | None = None
    username: str | None = None
    host: str | None = None
    port: int | None = None
    service_name: str | None = None
    sid: str | None = None
    dsn: str | None = None
    password_keyring_service: str | None = None
    password_keyring_username: str | None = None


def build_database_connection_uri(settings: DatabaseConnectionSettings) -> str:
    password = _read_keyring_password(settings)
    if settings.connection_uri:
        return _inject_password(str(settings.connection_uri).strip(), password)

    driver = str(settings.driver_class_name or "oracle+oracledb").strip()
    username = _require(settings.username, "db_username")
    if password is None:
        raise ValueError("A keyring password is required when db_connection_uri is not provided.")

    user_part = quote_plus(username)
    password_part = quote_plus(password)

    if settings.dsn:
        return f"{driver}://{user_part}:{password_part}@{settings.dsn}"

    host = _require(settings.host, "db_host")
    port = int(settings.port or 1521)
    base_uri = f"{driver}://{user_part}:{password_part}@{host}:{port}/"

    if settings.service_name:
        return f"{base_uri}?service_name={quote_plus(str(settings.service_name))}"
    if settings.sid:
        return f"{base_uri}{quote_plus(str(settings.sid))}"

    raise ValueError("Configure db_service_name, db_sid, or db_dsn for Oracle access.")


def _read_keyring_password(settings: DatabaseConnectionSettings) -> str | None:
    service = _clean_optional(settings.password_keyring_service)
    username = _clean_optional(settings.password_keyring_username or settings.username)
    if not service and not username:
        return None
    if not service or not username:
        raise ValueError("Both db_password_keyring_service and db_password_keyring_username are required.")

    try:
        import keyring
    except ImportError as exc:
        raise RuntimeError("Secure database access requires keyring. Install it with: pip install keyring") from exc

    password = keyring.get_password(service, username)
    if password is None:
        raise RuntimeError(
            f"No password found in keyring for service '{service}' and username '{username}'. "
            "Run scripts/store_keyring_secret.py to store it."
        )
    return password


def _inject_password(connection_uri: str, password: str | None) -> str:
    if password is None:
        return connection_uri
    if "{password}" in connection_uri:
        return connection_uri.replace("{password}", quote_plus(password))
    if "{password_raw}" in connection_uri:
        return connection_uri.replace("{password_raw}", password)
    return connection_uri


def _require(value: str | None, name: str) -> str:
    cleaned = _clean_optional(value)
    if not cleaned:
        raise ValueError(f"{name} is required for database access.")
    return cleaned


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
