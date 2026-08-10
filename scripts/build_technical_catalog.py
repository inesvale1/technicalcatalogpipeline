"""Fase 1 - Pipeline de Catalogo Tecnico: extrai metadados fisicos (Oracle / CSV /
S3 / Athena) e gera metadata_context_<esquema>.json para cada esquema configurado.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    package_parent = Path(__file__).resolve().parent.parent
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))

from core.context_builder import MetadataContextBuilder
from core.metadata_sources import build_metadata_source
from core.secure_credentials import DatabaseConnectionSettings


def _resolve_path(value: str, root: Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _load_config(path: str) -> dict:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    # config/catalog.config.json -> technicalcatalogpipeline/ -> Implementation/
    data["_workspace_root"] = config_path.parent.parent.parent
    data["_config_dir"] = config_path.parent
    return data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrai metadados tecnicos e gera metadata_context_<esquema>.json para os esquemas configurados."
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent.parent / "config" / "catalog.config.json"),
        help="Caminho para o catalog.config.json (default: config/catalog.config.json)",
    )
    parser.add_argument(
        "--schema",
        action="append",
        default=None,
        help="Processa so este esquema (pode repetir --schema para varios), substituindo a lista 'schemas' do config. "
        "Usado por outros programas (ex: dataquality) para pedir o contexto de um unico esquema sob demanda.",
    )
    args = parser.parse_args()

    config = _load_config(args.config)
    workspace_root: Path = config["_workspace_root"]
    config_dir: Path = config["_config_dir"]

    schema_root = _resolve_path(str(config.get("schema_root", "schema")), workspace_root)
    schemas = list(args.schema) if args.schema else list(config.get("schemas", []))
    if not schemas:
        print("Nenhum esquema configurado em 'schemas' (nem passado via --schema). Nada a fazer.")
        return

    metadata_source_type = str(config.get("metadata_source", "csv"))
    columns_to_delete = list(config.get("delete_cols", []))

    query_file = config.get("metadata_query_file")
    query_file_path = str(_resolve_path(query_file, config_dir)) if query_file else None

    connection_settings = DatabaseConnectionSettings(
        connection_uri=config.get("db_connection_uri"),
        driver_class_name=config.get("db_driver_class_name"),
        username=config.get("db_username"),
        host=config.get("db_host"),
        port=config.get("db_port"),
        service_name=config.get("db_service_name"),
        sid=config.get("db_sid"),
        dsn=config.get("db_dsn"),
        password_keyring_service=config.get("db_password_keyring_service"),
        password_keyring_username=config.get("db_password_keyring_username"),
    )

    metadata_source = build_metadata_source(
        source_type=metadata_source_type,
        base_folder=schema_root,
        columns_to_delete=columns_to_delete,
        connection_settings=connection_settings,
        schemas=list(config.get("metadata_db_schemas") or schemas),
        db_type=str(config.get("db_type", "Oracle")),
        query_template=config.get("metadata_query_template"),
        query_file=query_file_path,
        s3_uri=config.get("metadata_s3_uri"),
        s3_storage_options=dict(config.get("s3_storage_options") or {}),
        athena_databases=config.get("athena_databases"),
        athena_workgroup=str(config.get("athena_workgroup", "primary")),
        athena_s3_output=config.get("athena_s3_output"),
        aws_region=config.get("aws_region"),
    )

    print(f"Fonte de metadados: {metadata_source_type}")
    print(f"Schema root: {schema_root}")
    dfs = metadata_source.get_metadata_by_schema()
    print(f"Esquemas carregados pela fonte: {sorted(dfs.keys())}")

    print()
    print("Resumo:")
    results: list[tuple[str, str]] = []
    for schema_name in schemas:
        if schema_name not in dfs:
            print(f"  [ERRO   ] {schema_name} - metadados nao encontrados na fonte configurada")
            results.append((schema_name, "error"))
            continue

        output_dir = schema_root / schema_name / "inputs"
        builder = MetadataContextBuilder(
            schema_name=schema_name,
            df_schema_metadata=dfs[schema_name],
            output_dir=output_dir,
        )
        output_path = builder.build_and_save()
        print(f"  [OK     ] {schema_name} -> {output_path}")
        results.append((schema_name, "ok"))

    failures = [name for name, status in results if status == "error"]
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
