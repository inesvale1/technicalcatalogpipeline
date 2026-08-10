from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pandas as pd

from .schema_loader import schemaLoader


def get_current_telemetry():
    """Standalone stub: see schema_loader.get_current_telemetry."""
    return None


_METADATA_QUERY = """
SELECT
    c.table_schema                                              AS OWNER,
    c.table_name                                               AS TABLE_NAME,
    CAST(NULL AS BIGINT)                                       AS NUM_ROWS,
    c.ordinal_position                                         AS COLUMN_ID,
    c.column_name                                              AS COLUMN_NAME,
    c.data_type                                                AS DATA_TYPE,
    CAST(c.character_maximum_length AS INTEGER)                AS DATA_LENGTH,
    CASE c.is_nullable WHEN 'YES' THEN 'Y' ELSE 'N' END       AS NULLABLE,
    'N'                                                        AS DEFAULT_ON_NULL,
    CAST(NULL AS VARCHAR)                                      AS CONSTRAINTS,
    CAST(NULL AS VARCHAR)                                      AS IS_PK,
    CAST(NULL AS VARCHAR)                                      AS IS_FK,
    CAST(NULL AS VARCHAR)                                      AS IS_UNIQUE,
    c.comment                                                  AS COL_COMMENTS,
    t.comment                                                  AS TAB_COMMENTS,
    CAST(NULL AS BIGINT)                                       AS NUM_DISTINCT,
    CAST(NULL AS BIGINT)                                       AS NUM_NULLS,
    CAST(NULL AS DOUBLE)                                       AS AVG_COL_LEN,
    CAST(NULL AS INTEGER)                                      AS NUM_BUCKETS,
    CAST(NULL AS DOUBLE)                                       AS DENSITY
FROM information_schema.columns c
LEFT JOIN information_schema.tables t
    ON  c.table_schema = t.table_schema
    AND c.table_name   = t.table_name
WHERE c.table_schema = '{database}'
ORDER BY c.table_name, c.ordinal_position
"""


class AthenaMetadataSource:
    """Read schema metadata from AWS Glue Data Catalog via Athena.

    Queries information_schema.columns / information_schema.tables for each
    Glue database listed in `databases`. Columns not available in Glue (IS_PK,
    IS_FK, IS_UNIQUE, NUM_ROWS, NUM_NULLS, NUM_DISTINCT) are returned as NULL.

    Requires: pip install awswrangler
    AWS credentials must be available via environment variables, ~/.aws/credentials
    or an IAM role attached to the execution environment.
    """

    def __init__(
        self,
        databases: list[str] | str,
        workgroup: str = "primary",
        s3_output: str | None = None,
        aws_region: str | None = None,
        columns_to_delete: list[str] | None = None,
    ):
        if isinstance(databases, str):
            databases = [databases]
        self.databases = [str(d).strip() for d in databases if str(d).strip()]
        self.workgroup = str(workgroup or "primary").strip()
        self.s3_output = s3_output
        self.aws_region = aws_region
        self.columns_to_delete = columns_to_delete or []

    def get_metadata_by_schema(self) -> dict[str, pd.DataFrame]:
        try:
            import awswrangler as wr
        except ImportError as exc:
            raise RuntimeError(
                "Athena metadata source requires awswrangler. "
                "Install it with: pip install awswrangler"
            ) from exc

        if not self.databases:
            raise ValueError("At least one Glue database name is required for AthenaMetadataSource.")

        telemetry = get_current_telemetry()
        session = self._boto3_session()
        normalizer = schemaLoader(Path("."), self.columns_to_delete, auto_load=False)
        result: dict[str, pd.DataFrame] = {}

        for database in self.databases:
            sql = _METADATA_QUERY.format(database=database)
            kwargs: dict = dict(
                sql=sql,
                database=database,
                workgroup=self.workgroup,
                ctas_approach=False,
            )
            if session is not None:
                kwargs["boto3_session"] = session
            if self.s3_output:
                kwargs["s3_output"] = self.s3_output

            with (telemetry.stage("metadata.athena_load", schema=database) if telemetry is not None else nullcontext()):
                df = wr.athena.read_sql_query(**kwargs)
            df.columns = [str(c).strip().upper() for c in df.columns]
            df = normalizer.normalize_metadata_dataframe(df, f"athena:{database}")
            result[database] = normalizer._finalize_dataframe(df)
            print(f"[athena] Loaded {len(df)} column rows from Glue database '{database}'")

        return result

    def _boto3_session(self):
        if not self.aws_region:
            return None
        try:
            import boto3
            return boto3.Session(region_name=self.aws_region)
        except ImportError:
            return None
