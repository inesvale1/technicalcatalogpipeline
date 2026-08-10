# technicalcatalogpipeline

Fase 1 do diagrama de arquitetura: **Pipeline de Catálogo Técnico**.

Extrai metadados físicos de um esquema (schemas, tabelas, campos, constraints
PK/FK/UK, tipos, tamanhos, descrições) a partir de Oracle, CSV, S3 ou Athena/Glue,
e gera o contexto técnico consumido pelas etapas seguintes do fluxo:

```
schema/<esquema>/inputs/metadata_context_<esquema>.json
```

Esse arquivo é lido por:
- `dataquality` (fase de qualidade de metadados/dados), quando `regenerate_context: false`;
- `businessglossarypipeline` (Fase 2 — enriquecimento semântico e glossário de negócio).

## Origem

Este código é uma cópia — não um import — da lógica que hoje vive em
`dataquality/app/orchestration/metadata_context_builder.py` e
`dataquality/infrastructure/io/metadata_sources.py`. Segue o mesmo padrão de
desacoplamento total por arquivo já usado entre `dataquality` e
`businessglossarypipeline` (nenhum dos programas importa código de outro;
trocam apenas arquivos dentro de `schema/`). `technicalcatalogpipeline` passa a
ser o lugar oficial para (re)gerar `metadata_context_<esquema>.json` — a cópia
mantida dentro de `dataquality` continua funcionando (compatibilidade), mas
deixa de ser a fonte primária.

## Uso

```bash
pip install -r requirements.txt
python scripts/build_technical_catalog.py --config config/catalog.config.json
```

`config/catalog.config.json`:
- `schema_root`: pasta raiz onde ficam `<esquema>/inputs` e `<esquema>/outputs` (default `schema`, resolvido relativo à raiz do workspace).
- `schemas`: lista de esquemas a processar.
- `metadata_source`: `"csv"` (default, lê `metadata_<esquema>.csv` em `schema/<esquema>/inputs`), `"oracle"`, `"s3"` ou `"athena"`.
- Credenciais Oracle (`db_*`) e keyring seguem o mesmo formato do `dataquality` — grave a senha com `python scripts/store_keyring_secret.py --service <serviço> --username <usuário>`.

## Estrutura

```
core/
  context_builder.py         # MetadataContextBuilder — monta o JSON de contexto por tabela/coluna
  metadata_sources.py        # Csv/Database/S3/Athena metadata sources
  schema_loader.py           # normalização/leitura dos CSVs metadata_<esquema>.csv
  secure_credentials.py      # conexão Oracle via SQLAlchemy + keyring
  athena_metadata_source.py  # leitura via AWS Glue/Athena
config/
  catalog.config.json
  queries/metadata_query.sql # query Oracle customizável (opcional)
scripts/
  build_technical_catalog.py # CLI principal
  test_oracle_connection.py  # diagnóstico de conexão
  store_keyring_secret.py    # grava senha no keyring do SO
```

## Fora de escopo (por enquanto)

- Conexão direta a banco de dados para leitura/gravação fora do fluxo `schema/` por arquivo — hoje `csv` é a fonte usada em produção; `oracle`/`s3`/`athena` já estão implementados e prontos para quando a conexão direta for habilitada.
