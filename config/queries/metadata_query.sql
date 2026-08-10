-- -------------------------------------------------------------------------
-- Place the custom SELECT statement for reading Oracle metadata here.
--
-- Required elements:
-- 1. The placeholder {owners_filter} must appear exactly once in the WHERE clause
-- and will be replaced at runtime by a comma-separated list of owners.
-- Example: WHERE c.owner IN ({owners_filter})
--
-- 2. The result must contain at least the following columns (case-insensitive names):
-- OWNER, TABLE_NAME, COLUMN_NAME, DATA_TYPE, NULLABLE
--
-- 3. Optional columns but used by the framework:
-- COLUMN_ID, DATA_LENGTH, DATA_SCALE, AVG_COL_LEN,
-- NUM_DISTINCT, NUM_NULLS, NUM_ROWS, TABLE_ROWS, ROW_COUNT,
-- COL_COMMENTS, TAB_COMMENTS, CONSTRAINTS, DEFAULT_ON_NULL
--
-- Reference: the framework's default query is in
-- technicalcatalogpipeline/core/metadata_sources.py -> _default_query_template()
--
-- To enable this file, configure it in config/catalog.config.json:
-- "metadata_query_file": "config/queries/metadata_query.sql"
-- -------------------------------------------------------------------------

SELECT
    atc.OWNER,
    atc.TABLE_NAME,
    atcm.COMMENTS as tab_comments,
    at.NUM_ROWS,
    atc.COLUMN_ID,
    atc.COLUMN_NAME,
    atc.DATA_TYPE,
    atc.DATA_LENGTH,
    atc.NULLABLE,
    atc.DATA_SCALE,
    atc.AVG_COL_LEN,
    atc.NUM_DISTINCT,
    atc.NUM_NULLS,
    atc.DEFAULT_ON_NULL,
    accm.COMMENTS as col_comments,
    LISTAGG(
        CASE
            WHEN ac.CONSTRAINT_TYPE = 'P' THEN acc.CONSTRAINT_NAME || ' (PRIMARY KEY: ' || ac.STATUS || ')'
            WHEN ac.CONSTRAINT_TYPE = 'U' THEN acc.CONSTRAINT_NAME || ' (UNIQUE: ' || ac.STATUS || ')'
            WHEN ac.CONSTRAINT_TYPE = 'R' THEN acc.CONSTRAINT_NAME || ' (FOREIGN KEY: ' || ac.STATUS || ')'
            ELSE acc.CONSTRAINT_NAME || ' (' || ac.CONSTRAINT_TYPE || ': ' || ac.STATUS || ')'
        END,
        '- '
    ) WITHIN GROUP (ORDER BY acc.CONSTRAINT_NAME) AS CONSTRAINTS
FROM
    ALL_TAB_COLUMNS atc
LEFT JOIN
    ALL_TABLES at ON atc.OWNER = at.OWNER
                 AND atc.TABLE_NAME = at.TABLE_NAME
LEFT JOIN
    ALL_CONS_COLUMNS acc ON atc.OWNER = acc.OWNER
                        AND atc.TABLE_NAME = acc.TABLE_NAME
                        AND atc.COLUMN_NAME = acc.COLUMN_NAME
LEFT JOIN
    ALL_COL_COMMENTS accm ON accm.OWNER = atc.OWNER
                        AND accm.TABLE_NAME = atc.TABLE_NAME
                        AND accm.COLUMN_NAME = atc.COLUMN_NAME
LEFT JOIN
    ALL_TAB_COMMENTS atcm ON atcm.OWNER = atc.OWNER
                        AND atcm.TABLE_NAME = atc.TABLE_NAME
LEFT JOIN
    ALL_CONSTRAINTS ac ON acc.OWNER = ac.OWNER
                        AND acc.CONSTRAINT_NAME = ac.CONSTRAINT_NAME
                        AND acc.TABLE_NAME = ac.TABLE_NAME
WHERE
    UPPER(atc.TABLE_NAME) NOT LIKE 'RUPD$%' AND UPPER(atc.TABLE_NAME) NOT LIKE 'VW%' AND UPPER(atc.TABLE_NAME) NOT LIKE '%TMP%' AND UPPER(atc.TABLE_NAME) <> 'SUANOTA.NFP_DADOS_CADASTRAIS_HIST_BKP2' AND
    UPPER(atc.TABLE_NAME) NOT LIKE 'MLOG$_%' AND UPPER(atc.TABLE_NAME) NOT LIKE '%TEMP%' AND UPPER(atc.TABLE_NAME) <> 'NF_AVULSA_24012020_1500' AND
    UPPER(atc.TABLE_NAME) <> 'ADVOGADO_20100512' AND UPPER(atc.TABLE_NAME) NOT LIKE '%20180115' AND
    atc.OWNER IN ({owners_filter})
GROUP BY
    atc.OWNER,
    atc.TABLE_NAME,
    atcm.COMMENTS,
    at.NUM_ROWS,
    at.DUPLICATED,
    atc.COLUMN_ID,
    atc.COLUMN_NAME,
    atc.DATA_TYPE,
    atc.DATA_LENGTH,
    atc.NULLABLE,
    atc.DATA_SCALE,
    atc.AVG_COL_LEN,
    atc.NUM_DISTINCT,
    atc.NUM_NULLS,
    atc.DEFAULT_ON_NULL,
    accm.COMMENTS
ORDER BY
    atc.OWNER,
    atc.TABLE_NAME,
    atc.COLUMN_NAME;
