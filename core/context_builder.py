from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class MetadataContextBuilder:
    schema_name: str
    df_schema_metadata: pd.DataFrame
    output_dir: Path

    def build_and_save(self, payload: dict[str, Any] | None = None) -> Path:
        payload = payload or self.build()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"metadata_context_{self.schema_name}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def build(self) -> dict[str, Any]:
        df = self.df_schema_metadata.copy()
        if df.empty:
            return {
                "schema_name": self.schema_name,
                "owner": self.schema_name.upper(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tables": [],
                "columns": [],
            }

        owner = str(df["OWNER"].dropna().astype(str).iloc[0]).upper()
        db_instance_name = self._first_non_empty(df.get("DB_INSTANCE_NAME", pd.Series(dtype=str)))
        pk_lookup = self._build_pk_lookup(df)
        table_contexts: list[dict[str, Any]] = []
        column_contexts: list[dict[str, Any]] = []

        for table_name, table_df in df.groupby("TABLE_NAME", sort=True):
            table_df = table_df.sort_values(by="COLUMN_ID") if "COLUMN_ID" in table_df.columns else table_df.copy()
            table_context = self._build_table_context(owner, str(table_name), table_df, pk_lookup)
            table_contexts.append(table_context)
            for _, row in table_df.iterrows():
                column_contexts.append(self._build_column_context(owner, str(table_name), table_df, row, table_context, pk_lookup))

        return {
            "schema_name": self.schema_name,
            "owner": owner,
            "db_instance_name": db_instance_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tables": table_contexts,
            "columns": column_contexts,
        }

    def _build_pk_lookup(self, df: pd.DataFrame) -> dict[str, list[str]]:
        lookup: dict[str, list[str]] = {}
        key_candidates = df[df["IS_PK"].astype(bool) | df["IS_UNIQUE"].astype(bool)]
        for _, row in key_candidates.iterrows():
            column_name = str(row.get("COLUMN_NAME", "")).upper()
            table_name = str(row.get("TABLE_NAME", "")).upper()
            if not column_name or not table_name:
                continue
            lookup.setdefault(column_name, [])
            if table_name not in lookup[column_name]:
                lookup[column_name].append(table_name)
        return lookup

    def _build_table_context(
        self,
        owner: str,
        table_name: str,
        table_df: pd.DataFrame,
        pk_lookup: dict[str, list[str]],
    ) -> dict[str, Any]:
        primary_keys = self._collect_columns(table_df, "IS_PK")
        foreign_keys = self._collect_columns(table_df, "IS_FK")
        table_comment = self._first_non_empty(table_df.get("TAB_COMMENTS", pd.Series(dtype=str)))
        db_instance_name = self._first_non_empty(table_df.get("DB_INSTANCE_NAME", pd.Series(dtype=str)))
        existing_column_comments = {
            str(row["COLUMN_NAME"]): str(row["COL_COMMENTS"]).strip()
            for _, row in table_df.iterrows()
            if self._has_text(row.get("COL_COMMENTS"))
        }
        related_tables = self._infer_related_tables(table_name, foreign_keys, pk_lookup)
        row_count = self._safe_int(table_df.get("NUM_ROWS", pd.Series(dtype=int)).max() if "NUM_ROWS" in table_df.columns else 0)

        return {
            "owner": owner,
            "table_name": table_name,
            "db_instance_name": db_instance_name,
            "table_comment": table_comment,
            "table_type_inference": self._infer_table_type(table_name, table_df, row_count),
            "primary_keys": primary_keys,
            "foreign_keys": foreign_keys,
            "main_columns": self._select_main_columns(table_df, primary_keys, foreign_keys),
            "related_tables": related_tables,
            "row_count": row_count,
            "column_name_keywords": self._extract_keywords(table_df["COLUMN_NAME"].tolist()),
            "existing_column_comments": existing_column_comments,
        }

    def _build_column_context(
        self,
        owner: str,
        table_name: str,
        table_df: pd.DataFrame,
        row: pd.Series,
        table_context: dict[str, Any],
        pk_lookup: dict[str, list[str]],
    ) -> dict[str, Any]:
        column_name = str(row.get("COLUMN_NAME", ""))
        references = self._infer_reference(table_name, column_name, pk_lookup)
        num_rows = self._safe_float(row.get("NUM_ROWS"))
        num_nulls = self._safe_float(row.get("NUM_NULLS"))
        null_ratio = None
        if num_rows and num_rows > 0:
            null_ratio = round(num_nulls / num_rows, 6)

        return {
            "owner": owner,
            "table_name": table_name,
            "table_comment": table_context.get("table_comment", ""),
            "column_name": column_name,
            "data_type": self._format_data_type(row),
            "nullable": "Y" if bool(row.get("NULLABLE", False)) else "N",
            "is_pk": bool(row.get("IS_PK", False)),
            "is_fk": bool(row.get("IS_FK", False)),
            "is_uk": bool(row.get("IS_UNIQUE", False)),
            "references": references,
            "column_neighbors": self._column_neighbors(table_df, column_name),
            "profile": {
                "num_distinct": self._safe_int(row.get("NUM_DISTINCT")),
                "null_ratio": null_ratio,
            },
        }

    def _collect_columns(self, table_df: pd.DataFrame, flag_column: str) -> list[str]:
        if flag_column not in table_df.columns:
            return []
        return [
            str(value)
            for value in table_df.loc[table_df[flag_column].astype(bool), "COLUMN_NAME"].tolist()
        ]

    def _infer_related_tables(self, table_name: str, foreign_keys: list[str], pk_lookup: dict[str, list[str]]) -> list[str]:
        related: list[str] = []
        for column_name in foreign_keys:
            reference = self._infer_reference(table_name, column_name, pk_lookup)
            ref_table = str(reference.get("table", "")).strip()
            if ref_table and ref_table not in related:
                related.append(ref_table)
        return related

    def _infer_reference(self, table_name: str, column_name: str, pk_lookup: dict[str, list[str]]) -> dict[str, str]:
        candidates = [candidate for candidate in pk_lookup.get(str(column_name).upper(), []) if candidate != table_name.upper()]
        if not candidates:
            return {}
        ordered = sorted(candidates, key=lambda candidate: self._reference_score(column_name, candidate), reverse=True)
        best_table = ordered[0]
        return {"table": best_table, "column": str(column_name).upper()}

    def _reference_score(self, column_name: str, table_name: str) -> int:
        tokens = set(self._tokenize_identifier(column_name))
        table_tokens = set(self._tokenize_identifier(table_name))
        return len(tokens & table_tokens)

    def _select_main_columns(self, table_df: pd.DataFrame, primary_keys: list[str], foreign_keys: list[str]) -> list[str]:
        priority_columns = primary_keys + foreign_keys
        scored: list[tuple[int, str]] = []
        for _, row in table_df.iterrows():
            column_name = str(row.get("COLUMN_NAME", ""))
            score = 0
            upper_name = column_name.upper()
            if column_name in priority_columns:
                score += 100
            if upper_name.startswith(("NOM_", "DSC_", "DAT_", "VLR_", "NUM_", "COD_", "SIT_", "STA_", "TIP_")):
                score += 20
            if not bool(row.get("NULLABLE", False)):
                score += 5
            scored.append((score, column_name))
        ordered = [column for _, column in sorted(scored, key=lambda item: (-item[0], item[1]))]
        result: list[str] = []
        for column_name in priority_columns + ordered:
            if column_name and column_name not in result:
                result.append(column_name)
            if len(result) >= 5:
                break
        return result

    def _extract_keywords(self, column_names: list[str]) -> list[str]:
        stop_tokens = {"COD", "NUM", "SEQ", "DAT", "DSC", "NOM", "VLR", "QTD", "TIP", "SIT", "STA", "TXT", "ID"}
        keywords: list[str] = []
        for column_name in column_names:
            for token in self._tokenize_identifier(column_name):
                if token in stop_tokens or len(token) <= 2:
                    continue
                if token not in keywords:
                    keywords.append(token)
                if len(keywords) >= 15:
                    return keywords
        return keywords

    def _infer_table_type(self, table_name: str, table_df: pd.DataFrame, row_count: int) -> str:
        name_tokens = set(self._tokenize_identifier(table_name))
        fk_count = int(table_df["IS_FK"].astype(bool).sum()) if "IS_FK" in table_df.columns else 0
        pk_count = int(table_df["IS_PK"].astype(bool).sum()) if "IS_PK" in table_df.columns else 0
        non_key_columns = int(len(table_df) - fk_count - pk_count)

        if name_tokens & {"LOG", "AUDIT", "AUDITORIA", "HIST", "HISTORICO", "TRILHA"}:
            return "log"
        if name_tokens & {"ITEM", "DET", "DETALHE"}:
            return "detalhe"
        if name_tokens & {"TIPO", "STATUS", "SITUACAO", "UF", "MUNICIPIO", "CATEGORIA", "PARAMETRO"}:
            return "referencia"
        if name_tokens & {"RESUMO", "AGREGADO", "TOTAL", "INDICADOR", "SUMARIO"}:
            return "agregacao"
        if fk_count >= 2 and non_key_columns <= 2:
            return "associacao"
        if fk_count >= 1 and any(str(value).upper().startswith(("DAT_", "VLR_", "STA_", "SIT_")) for value in table_df["COLUMN_NAME"]):
            return "transacao"
        if row_count <= 200 and fk_count == 0:
            return "referencia"
        return "cadastro"

    def _column_neighbors(self, table_df: pd.DataFrame, column_name: str) -> list[str]:
        ordered = table_df["COLUMN_NAME"].astype(str).tolist()
        try:
            position = ordered.index(column_name)
        except ValueError:
            return []
        start = max(position - 2, 0)
        end = min(position + 3, len(ordered))
        return [name for idx, name in enumerate(ordered[start:end], start=start) if idx != position]

    def _format_data_type(self, row: pd.Series) -> str:
        data_type = str(row.get("DATA_TYPE", "")).strip().upper()
        length = self._safe_int(row.get("DATA_LENGTH"), default=None)
        scale = self._safe_int(row.get("DATA_SCALE"), default=None)
        if data_type == "NUMBER" and length is not None and scale is not None:
            return f"{data_type}({length},{scale})"
        if data_type in {"VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"} and length is not None:
            return f"{data_type}({length})"
        return data_type

    def _tokenize_identifier(self, value: str) -> list[str]:
        return [token for token in re.split(r"[^A-Z0-9]+", str(value).upper()) if token]

    def _first_non_empty(self, values: pd.Series) -> str:
        for value in values.tolist():
            if self._has_text(value):
                return str(value).strip()
        return ""

    def _has_text(self, value: Any) -> bool:
        text = str(value).strip()
        return text.lower() not in {"", "nan", "none", "<na>", "null"}

    def _safe_int(self, value: Any, default: int | None = 0) -> int | None:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(numeric):
            return default
        return int(numeric)

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
        if pd.isna(numeric):
            return default
        return float(numeric)
