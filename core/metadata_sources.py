from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import pandas as pd

from .schema_loader import schemaLoader
from .secure_credentials import DatabaseConnectionSettings, build_database_engine


def get_current_telemetry():
    """Standalone stub: see schema_loader.get_current_telemetry."""
    return None


class MetadataSource(Protocol):
    def get_metadata_by_schema(self) -> dict[str, pd.DataFrame]:
        ...


class CsvMetadataSource:
    def __init__(self, base_folder: Path, columns_to_delete: list[str] | None = None):
        self.base_folder = Path(base_folder)
        self.columns_to_delete = columns_to_delete or []

    def get_metadata_by_schema(self) -> dict[str, pd.DataFrame]:
        return schemaLoader(self.base_folder, self.columns_to_delete).get_dictionary()


class DatabaseMetadataSource:
    def __init__(
        self,
        connection_settings: DatabaseConnectionSettings,
        schemas: list[str] | None = None,
        columns_to_delete: list[str] | None = None,
        db_type: str = "Oracle",
        query_template: str | None = None,
        query_file: str | None = None,
    ):
        self.connection_settings = connection_settings
        self.schemas = [_normalize_identifier(schema) for schema in (schemas or []) if str(schema).strip()]
        self.columns_to_delete = columns_to_delete or []
        self.db_type = db_type
        self.query_template = (
            _load_query_file(query_file)
            if query_file
            else (query_template or self._default_query_template(db_type))
        )

    def get_metadata_by_schema(self) -> dict[str, pd.DataFrame]:
        telemetry = get_current_telemetry()
        try:
            from sqlalchemy import text
        except ImportError as exc:
            raise RuntimeError("Database metadata source requires SQLAlchemy. Install it with: pip install sqlalchemy") from exc

        if not self.schemas:
            raise ValueError("metadata_db_schemas must contain at least one Oracle owner/schema.")

        query = self.query_template.format(owners_filter=self._build_owner_filter())
        normalizer = schemaLoader(Path("."), self.columns_to_delete, auto_load=False)

        with (telemetry.stage("metadata.oracle_load") if telemetry is not None else _nullcontext()):
            engine = build_database_engine(self.connection_settings)
            with engine.connect() as connection:
                df = pd.read_sql(text(query), connection)

        df.columns = [str(column).strip().upper() for column in df.columns]
        if telemetry is not None:
            telemetry.increment("metadata_oracle_queries_executed")
            telemetry.increment("metadata_rows_loaded", int(df.shape[0]))
        return normalizer._split_dataframe_by_schema(normalizer.normalize_metadata_dataframe(df, "oracle metadata query"))

    def _build_owner_filter(self) -> str:
        return ", ".join(f"'{schema}'" for schema in self.schemas)

    def _default_query_template(self, db_type: str) -> str:
        if str(db_type).strip().lower() != "oracle":
            raise ValueError("Only Oracle metadata queries are supported by the default DatabaseMetadataSource.")
        return """
SELECT
    c.owner AS owner,
    c.table_name AS table_name,
    NVL(t.num_rows, 0) AS num_rows,
    c.column_id AS column_id,
    c.column_name AS column_name,
    c.data_type AS data_type,
    c.data_length AS data_length,
    c.nullable AS nullable,
    'N' AS default_on_null,
    NVL(c.data_scale, 0) AS data_scale,
    NVL(c.avg_col_len, 0) AS avg_col_len,
    NVL(c.num_distinct, 0) AS num_distinct,
    NVL(c.num_nulls, 0) AS num_nulls,
    NVL(c.num_buckets, 0) AS num_buckets,
    NVL(t.num_rows, 0) AS table_rows,
    NVL(t.num_rows, 0) AS row_count,
    cc.comments AS col_comments,
    tc.comments AS tab_comments,
    LISTAGG(
        CASE ac.constraint_type
            WHEN 'P' THEN 'PRIMARY KEY'
            WHEN 'R' THEN 'FOREIGN KEY'
            WHEN 'U' THEN 'UNIQUE'
            ELSE ac.constraint_type
        END || ':' || ac.constraint_name,
        '; '
    ) WITHIN GROUP (ORDER BY ac.constraint_name) AS constraints
FROM all_tab_columns c
LEFT JOIN all_tables t
    ON t.owner = c.owner
    AND t.table_name = c.table_name
LEFT JOIN all_col_comments cc
    ON cc.owner = c.owner
    AND cc.table_name = c.table_name
    AND cc.column_name = c.column_name
LEFT JOIN all_tab_comments tc
    ON tc.owner = c.owner
    AND tc.table_name = c.table_name
LEFT JOIN all_cons_columns acc
    ON acc.owner = c.owner
    AND acc.table_name = c.table_name
    AND acc.column_name = c.column_name
LEFT JOIN all_constraints ac
    ON ac.owner = acc.owner
    AND ac.constraint_name = acc.constraint_name
    AND ac.table_name = acc.table_name
WHERE c.owner IN ({owners_filter})
GROUP BY
    c.owner,
    c.table_name,
    t.num_rows,
    c.column_id,
    c.column_name,
    c.data_type,
    c.data_length,
    c.nullable,
    c.data_scale,
    c.avg_col_len,
    c.num_distinct,
    c.num_nulls,
    c.num_buckets,
    cc.comments,
    tc.comments
ORDER BY c.owner, c.table_name, c.column_id
"""


class S3MetadataSource:
    def __init__(
        self,
        uri: str,
        columns_to_delete: list[str] | None = None,
        storage_options: dict[str, object] | None = None,
    ):
        self.uri = str(uri).strip()
        self.columns_to_delete = columns_to_delete or []
        self.storage_options = storage_options or {}

    def get_metadata_by_schema(self) -> dict[str, pd.DataFrame]:
        if not self.uri:
            raise ValueError("metadata_s3_uri is required when metadata_source='s3'.")

        normalizer = schemaLoader(Path("."), self.columns_to_delete, auto_load=False)
        if self.uri.lower().endswith((".csv", ".xlsx", ".xls")):
            df = self._read_file(self.uri)
            return normalizer._split_dataframe_by_schema(normalizer.normalize_metadata_dataframe(df, self.uri))

        dfs: dict[str, pd.DataFrame] = {}
        for uri in self._list_metadata_files(self.uri):
            schema_name = _schema_from_metadata_filename(uri)
            if not schema_name:
                continue
            df = normalizer.normalize_metadata_dataframe(self._read_file(uri), uri)
            dfs[schema_name] = normalizer._finalize_dataframe(df)
            normalizer._register_dataframe_telemetry(df, schema_name)
        return dfs

    def _read_file(self, uri: str) -> pd.DataFrame:
        if uri.lower().endswith(".csv"):
            return pd.read_csv(uri, storage_options=self.storage_options)
        return pd.read_excel(uri, storage_options=self.storage_options)

    def _list_metadata_files(self, uri: str) -> list[str]:
        try:
            import fsspec
        except ImportError as exc:
            raise RuntimeError("S3 metadata source requires fsspec/s3fs. Install it with: pip install s3fs") from exc

        normalized = uri.rstrip("/")
        fs, _, paths = fsspec.get_fs_token_paths(normalized, storage_options=self.storage_options)
        base_path = paths[0].rstrip("/")
        matches = fs.glob(f"{base_path}/metadados*.csv")
        return [f"s3://{match}" if not str(match).startswith("s3://") else str(match) for match in matches]


def build_metadata_source(
    source_type: str,
    base_folder: Path,
    columns_to_delete: list[str] | None = None,
    connection_settings: DatabaseConnectionSettings | None = None,
    schemas: list[str] | None = None,
    db_type: str = "Oracle",
    query_template: str | None = None,
    query_file: str | None = None,
    s3_uri: str | None = None,
    s3_storage_options: dict[str, object] | None = None,
    athena_databases: list[str] | None = None,
    athena_workgroup: str = "primary",
    athena_s3_output: str | None = None,
    aws_region: str | None = None,
) -> MetadataSource:
    normalized = str(source_type or "csv").strip().lower()
    if normalized == "csv":
        return CsvMetadataSource(base_folder, columns_to_delete)
    if normalized in {"database", "db", "oracle"}:
        if connection_settings is None:
            raise ValueError("Database connection settings are required when metadata_source='oracle'.")
        return DatabaseMetadataSource(
            connection_settings=connection_settings,
            schemas=schemas,
            columns_to_delete=columns_to_delete,
            db_type=db_type,
            query_template=query_template,
            query_file=query_file,
        )
    if normalized == "s3":
        return S3MetadataSource(str(s3_uri or ""), columns_to_delete, s3_storage_options)
    if normalized == "athena":
        from .athena_metadata_source import AthenaMetadataSource
        databases = athena_databases or schemas or []
        if not databases:
            raise ValueError(
                "athena_databases or metadata_db_schemas is required when metadata_source='athena'."
            )
        return AthenaMetadataSource(
            databases=databases,
            workgroup=athena_workgroup,
            s3_output=athena_s3_output,
            aws_region=aws_region,
            columns_to_delete=columns_to_delete,
        )
    raise ValueError(f"Unsupported metadata_source: {source_type}")


def _normalize_identifier(value: str) -> str:
    text = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", text):
        raise ValueError(f"Invalid Oracle identifier: {value}")
    return text


def _schema_from_metadata_filename(uri: str) -> str | None:
    name = Path(str(uri).split("/")[-1]).stem
    if name.lower() == "metadados":
        return None
    match = re.match(r"^metadata_(.+)$", name, flags=re.IGNORECASE)
    if not match:
        return None
    return re.sub(r"[^0-9a-zA-Z_]+", "_", match.group(1)).strip("_").lower()


def _load_query_file(path: str) -> str:
    """Read a SQL query from a file.  The file must contain a single SELECT statement
    with the placeholder {owners_filter} where the IN-list of schema names will be
    injected (e.g. ``WHERE c.owner IN ({owners_filter})``).
    """
    query_path = Path(path)
    if not query_path.exists():
        raise FileNotFoundError(
            f"metadata_query_file not found: {query_path.resolve()}"
        )
    return query_path.read_text(encoding="utf-8")


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc_info):
        return False
