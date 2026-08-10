from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


def get_current_telemetry():
    """Standalone stub: technicalcatalogpipeline does not share dataquality's
    telemetry collector. Kept as a no-op so the loading logic below (copied
    from dataquality/infrastructure/io/csv/schema_loader.py) is unchanged.
    """
    return None


class schemaLoader:
    """Load metadata CSV files (metadata_*.csv) from a base folder.

    The loader walks all subfolders under `base_folder`, finds CSV files matching
    `metadata_<schema>.csv`, loads each file into a typed DataFrame, and stores
    the result in a dictionary keyed by `<schema>`.

    This implementation is intentionally CSV-based, so you can later replace it
    with an Oracle-backed loader while keeping the same interface.
    """

    REQUIRED = [
        "OWNER",
        "TABLE_NAME",
        "NUM_ROWS",
        "COLUMN_NAME",
        "DATA_TYPE",
        "NULLABLE",
        "DEFAULT_ON_NULL",
        "CONSTRAINTS",
        "IS_PK",
        "IS_FK",
        "IS_UNIQUE",
    ]

    STRING_COLS = [
        "DB_INSTANCE_NAME",
        "OWNER",
        "TABLE_NAME",
        "COLUMN_NAME",
        "DATA_TYPE",
        "COL_COMMENTS",
        "TAB_COMMENTS",
        "CONSTRAINTS",
        "DUPLICATED",
    ]
    INT_COLS = [
        "NUM_ROWS",
        "COLUMN_ID",
        "DATA_LENGTH",
        "DATA_SCALE",
        "AVG_COL_LEN",
        "NUM_DISTINCT",
        "NUM_NULLS",
        "NUM_BUCKETS",
        "TABLE_ROWS",
        "ROW_COUNT",
    ]
    DOUBLE_COLS = []
    BOOL_COLS = ["NULLABLE", "DEFAULT_ON_NULL", "IS_PK", "IS_FK", "IS_UNIQUE"]

    _TRUE_TOKENS = {"Y", "YES", "SIM", "S", "1", "TRUE", "VERDADE", "VERDADEIRO", "T", "ON"}
    _FALSE_TOKENS = {"N", "NO", "NÃO", "NAO", "0", "FALSE", "FALSO", "F", "OFF"}

    # Detects accented characters silently replaced by '?' during a bad Oracle
    # extraction (e.g. NLS/charset mismatch): "Ac?o Fiscal", "N?o", "Descric?o".
    _MID_WORD_QUESTION_MARK = re.compile(r"[A-Za-zÀ-ÿ]\?[A-Za-zÀ-ÿ]")
    # Detects accented characters silently dropped instead of substituted
    # (e.g. "CESTA BASICA" with no accent at all where "BÁSICA" is expected).
    _ACCENTED_CHARS = set("ÁÉÍÓÚÃÕÂÊÔÀÇáéíóúãõâêôàç")
    _MIN_TEXT_LENGTH_FOR_ACCENT_CHECK = 500

    def __init__(self, base_folder: Path, columns_to_delete: Optional[List[str]] = None, auto_load: bool = True):
        self.base_folder: Path = Path(base_folder)
        self.columns_to_delete = columns_to_delete or []
        self.dictionary: Dict[str, pd.DataFrame] = self._read_csv_tree() if auto_load else {}

    def get_dictionary(self) -> Dict[str, pd.DataFrame]:
        return self.dictionary

    def normalize_metadata_dataframe(self, df: pd.DataFrame, source_name: str = "<dataframe>") -> pd.DataFrame:
        df = df.copy()
        df.columns = [str(c).strip().upper() for c in df.columns]

        if "COL_COMMENTS" not in df.columns:
            df["COL_COMMENTS"] = pd.NA
        if "TAB_COMMENTS" not in df.columns:
            df["TAB_COMMENTS"] = pd.NA
        if "CONSTRAINTS" not in df.columns:
            df["CONSTRAINTS"] = ""
        if "DB_INSTANCE_NAME" not in df.columns:
            df["DB_INSTANCE_NAME"] = pd.NA

        df["CONSTRAINTS_NORM"] = df["CONSTRAINTS"].astype(str).str.upper().fillna("")

        df["IS_PK"] = df["CONSTRAINTS_NORM"].str.contains("PRIMARY KEY", na=False)
        df["IS_FK"] = df["CONSTRAINTS_NORM"].str.contains("FOREIGN KEY", na=False)
        df["IS_UNIQUE"] = df["CONSTRAINTS_NORM"].str.contains("UNIQUE", na=False)

        missing = [c for c in self.REQUIRED if c not in df.columns]
        if missing:
            raise ValueError(f"Metadata source {source_name} is missing required columns: {missing}")

        df.drop(columns=["CONSTRAINTS_NORM"], inplace=True)

        for c in (set(self.STRING_COLS + self.INT_COLS + self.DOUBLE_COLS + self.BOOL_COLS) - set(df.columns)):
            df[c] = pd.NA

        for c in self.STRING_COLS:
            if c in df.columns:
                df[c] = df[c].astype(str)

        for c in self.INT_COLS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)

        for c in self.DOUBLE_COLS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).astype(float)

        for c in self.BOOL_COLS:
            if c in df.columns:
                df[c] = df[c].map(self._to_bool).astype(bool)

        desired = self.STRING_COLS + self.INT_COLS + self.DOUBLE_COLS + self.BOOL_COLS
        ordered = [c for c in desired if c in df.columns] + [c for c in df.columns if c not in desired]
        return df[ordered]

    # ---------------- internal helpers ----------------

    def save_individual_csvs(self, base_folder: Optional[Path] = None) -> None:
        """Save each schema's DataFrame as metadata_<schema>.csv inside its own subfolder."""
        output_root = Path(base_folder) if base_folder else self.base_folder
        for schema_name, df in self.dictionary.items():
            schema_dir = output_root / schema_name
            schema_dir.mkdir(parents=True, exist_ok=True)
            csv_path = schema_dir / f"metadata_{schema_name}.csv"
            df.to_csv(csv_path, index=False, sep=";", encoding="utf-8-sig")

    def _read_csv_tree(self) -> Dict[str, pd.DataFrame]:
        if not self.base_folder.exists():
            raise FileNotFoundError(f"Base folder not found: {self.base_folder}")

        dfs: Dict[str, pd.DataFrame] = {}
        # Agora o padrão aceita apenas CSV
        pattern = re.compile(r"^metadata_(.+)\.csv$", flags=re.IGNORECASE)

        for root, _, files in os.walk(self.base_folder):
            for fname in files:
                m = pattern.match(fname)
                if not m:
                    continue

                suffix_raw = m.group(1)
                suffix = self._sanitize_suffix(suffix_raw)

                csv_path = Path(root) / fname

                # segurança extra: garante que é CSV
                if csv_path.suffix.lower() != ".csv":
                    continue

                df = self._load_and_typed_file(csv_path)

                df = self._finalize_dataframe(df)
                dfs[suffix] = df
                self._register_dataframe_telemetry(df, suffix)
                self._check_encoding_corruption(df, suffix)

        return dfs

    def _split_dataframe_by_schema(self, df_all: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        owners = df_all["OWNER"].astype(str).str.strip()
        valid_mask = owners.ne("") & owners.str.lower().ne("nan")
        if not valid_mask.any():
            raise ValueError("Unified metadata file does not contain valid OWNER values.")

        dfs: Dict[str, pd.DataFrame] = {}
        for owner_name, owner_df in df_all.loc[valid_mask].groupby(owners.loc[valid_mask].str.upper(), sort=True):
            suffix = self._sanitize_suffix(str(owner_name))
            df = self._finalize_dataframe(owner_df.copy())
            dfs[suffix] = df
            self._register_dataframe_telemetry(df, suffix)
            self._check_encoding_corruption(df, suffix)
        return dfs

    def _finalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.columns_to_delete:
            df = df.drop(
                columns=[c for c in self.columns_to_delete if c in df.columns],
                errors="ignore"
            )
        return df

    def _register_dataframe_telemetry(self, df: pd.DataFrame, schema_name: str) -> None:
        telemetry = get_current_telemetry()
        if telemetry is None:
            return
        telemetry.increment("metadata_files_loaded")
        telemetry.increment("metadata_rows_loaded", int(df.shape[0]))
        telemetry.increment("metadata_loader_tables_detected", int(df["TABLE_NAME"].nunique()), schema=schema_name)
        telemetry.set_gauge("input_columns", int(df.shape[0]), schema=schema_name)
        telemetry.set_gauge("input_tables", int(df["TABLE_NAME"].nunique()), schema=schema_name)

    def _check_encoding_corruption(self, df: pd.DataFrame, schema_name: str) -> None:
        """Warn when TAB_COMMENTS/COL_COMMENTS show signs of an Oracle-extraction
        encoding mismatch. Detection only: the bytes are already lost by this point,
        so the fix is re-extracting the schema after correcting the Oracle
        connection's charset/NLS settings, not anything this loader can repair.
        """
        text_parts: list[str] = []
        for col in ("TAB_COMMENTS", "COL_COMMENTS"):
            if col in df.columns:
                text_parts.extend(str(v) for v in df[col].dropna().tolist())
        combined_text = " ".join(text_parts)
        if not combined_text.strip():
            return

        question_mark_matches = self._MID_WORD_QUESTION_MARK.findall(combined_text)
        if question_mark_matches:
            examples = sorted(set(question_mark_matches))[:5]
            self._report_corruption_telemetry(
                "encoding_corruption_questionmark_matches", len(question_mark_matches), schema_name
            )

        if (
            len(combined_text) >= self._MIN_TEXT_LENGTH_FOR_ACCENT_CHECK
            and not any(ch in self._ACCENTED_CHARS for ch in combined_text)
        ):
            self._report_corruption_telemetry("encoding_corruption_missing_accents", 1, schema_name)

    def _report_corruption_telemetry(self, metric: str, value: int, schema_name: str) -> None:
        telemetry = get_current_telemetry()
        if telemetry is None:
            return
        telemetry.increment(metric, value, schema=schema_name)

    def _sanitize_suffix(self, suffix: str) -> str:
        return re.sub(r"[^0-9a-zA-Z_]+", "_", suffix).strip("_").lower()

    def _to_bool(self, v: Any) -> bool:
        if pd.isna(v):
            return False
        s = str(v).strip().upper()
        if s in self._TRUE_TOKENS:
            return True
        if s in self._FALSE_TOKENS:
            return False
        # fallback: numeric truthiness
        try:
            return bool(int(float(s)))
        except Exception:
            return False

    def _read_csv_with_fallback(self, path):
        encodings = ["utf-8-sig", "cp1252", "latin1"]
        seps = [",", ";", "\t", "|"]

        last_err: Exception | None = None
        for enc in encodings:
            for sep in seps:
                try:
                    df = pd.read_csv(path, encoding=enc, sep=sep, quotechar='"')
                    # Heurística: se leu apenas 1 coluna e o header contém ;, provavelmente separador errado
                    if df.shape[1] == 1 and ";" in df.columns[0]:
                        continue
                    return df
                except (UnicodeDecodeError, pd.errors.ParserError) as e:
                    last_err = e

        raise last_err if last_err else RuntimeError(f"Could not read CSV: {path}")

    def _load_and_typed_file(self, path: Path) -> pd.DataFrame:
        df = self._read_csv_with_fallback(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
        return self.normalize_metadata_dataframe(df, path.name)
