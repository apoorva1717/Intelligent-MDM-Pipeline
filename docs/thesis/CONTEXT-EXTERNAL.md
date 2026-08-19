# External System Context

Systems that form part of the implemented pipeline but do not live in this repository:
Azure Data Factory (Tillit tenant), Azure SQL Managed Instance stored procedures, and the
DATAshaper configuration (Tillit SaaS, no file export available).

Provenance is marked per section:

- **[EXPORT]** — verbatim artefact export. Citable as ground truth.
- **[OBSERVED]** — recorded from the DATAshaper Studio user interface. Citable as an
  observation with the date of observation. Not a file artefact.
- **[AUTHOR]** — stated by the author. Requires confirmation against the system before the
  code freeze.

Observations recorded 2026-08-16.

---

## 1 · Entity and group code

**[OBSERVED]** DS Studio → Batch processing → Process overview shows entity `test_77` with a
group-code selector listing `TEST2`, `TEST3`, `TEST4`, `TEST5`, `TEST6`, `TEST7`, `TEST8`,
`TEST10`. The process table columns `Entity` and `Group code` show `test_77` / `TEST8` for
each run.

Therefore `test_77` is the **entity**, realised as the SQL schema name. The **group code**
identifies one import. Legacy and Validation hold records from all group codes under the
entity, so group code is the required scoping predicate for any per-import processing.

**[OBSERVED]** DS process task names visible in the Process overview, most recent last:
`LegacyMapping`, `MigrateData`, `ProcessValidation`, plus two further tasks above them in the
list. All completed with result `1/1` on 12/08/2026.

**[OBSERVED]** Record codes carry the group code as a prefix: `TEST7_41000009`,
`TEST10_42000001`, `TEST10_44000003`.

---

## 2 · ADF pipeline: Enrichment

**[EXPORT]** Published 2026-07-29T12:09:37Z. Reproduced verbatim.

```json
{
    "name": "Enrichment Pipeline",
    "properties": {
        "activities": [
            {
                "name": "Lookup2",
                "type": "Lookup",
                "dependsOn": [],
                "policy": {
                    "timeout": "0.12:00:00",
                    "retry": 0,
                    "retryIntervalInSeconds": 30,
                    "secureOutput": false,
                    "secureInput": false
                },
                "userProperties": [],
                "typeProperties": {
                    "source": {
                        "type": "SqlMISource",
                        "sqlReaderQuery": {
                            "value": "WITH n AS (\n    SELECT TOP (SELECT COUNT(*) FROM test_77.Legacy)\n           ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS rn\n    FROM test_77.Legacy\n)\nSELECT rn * 50 AS offset\nFROM n\nWHERE rn * 50 < (SELECT COUNT(*) FROM test_77.Legacy);",
                            "type": "Expression"
                        },
                        "partitionOption": "None"
                    },
                    "dataset": {
                        "referenceName": "AzureSqlMITable1",
                        "type": "DatasetReference"
                    },
                    "firstRowOnly": false
                }
            },
            {
                "name": "ForEach1",
                "type": "ForEach",
                "dependsOn": [
                    { "activity": "Lookup2", "dependencyConditions": ["Succeeded"] }
                ],
                "userProperties": [],
                "typeProperties": {
                    "items": {
                        "value": "@activity('Lookup2').output.value",
                        "type": "Expression"
                    },
                    "isSequential": true,
                    "activities": [
                        {
                            "name": "Lookup1",
                            "type": "Lookup",
                            "dependsOn": [],
                            "policy": {
                                "timeout": "0.12:00:00",
                                "retry": 0,
                                "retryIntervalInSeconds": 30,
                                "secureOutput": false,
                                "secureInput": false
                            },
                            "userProperties": [],
                            "typeProperties": {
                                "source": {
                                    "type": "SqlMISource",
                                    "sqlReaderQuery": {
                                        "value": "SELECT * FROM test_77.Legacy ORDER BY Customer\nOFFSET @{item().offset} ROWS FETCH NEXT 50 ROWS ONLY",
                                        "type": "Expression"
                                    },
                                    "partitionOption": "None"
                                },
                                "dataset": {
                                    "referenceName": "AzureSqlMITable1",
                                    "type": "DatasetReference"
                                },
                                "firstRowOnly": false
                            }
                        },
                        {
                            "name": "Web1",
                            "type": "WebActivity",
                            "dependsOn": [
                                { "activity": "Lookup1", "dependencyConditions": ["Succeeded"] }
                            ],
                            "policy": {
                                "timeout": "0.12:00:00",
                                "retry": 0,
                                "retryIntervalInSeconds": 30,
                                "secureOutput": false,
                                "secureInput": false
                            },
                            "userProperties": [],
                            "typeProperties": {
                                "method": "POST",
                                "headers": { "Content-Type": "application/json" },
                                "url": "https://mdm-pipeline-api.azurewebsites.net/enrich",
                                "connectVia": {
                                    "referenceName": "AutoResolveIntegrationRuntime",
                                    "type": "IntegrationRuntimeReference"
                                },
                                "body": {
                                    "value": "@json(concat('{\"records\":', string(activity('Lookup1').output.value), '}'))",
                                    "type": "Expression"
                                }
                            }
                        },
                        {
                            "name": "Merge Back",
                            "type": "SqlServerStoredProcedure",
                            "dependsOn": [
                                { "activity": "Web1", "dependencyConditions": ["Succeeded"] }
                            ],
                            "policy": {
                                "timeout": "0.12:00:00",
                                "retry": 0,
                                "retryIntervalInSeconds": 30,
                                "secureOutput": false,
                                "secureInput": false
                            },
                            "userProperties": [],
                            "typeProperties": {
                                "storedProcedureName": "dbo.usp_merge_legacy_enriched",
                                "storedProcedureParameters": {
                                    "payload": {
                                        "value": {
                                            "value": "@string(activity('Web1').output)",
                                            "type": "Expression"
                                        },
                                        "type": "String"
                                    }
                                }
                            },
                            "linkedServiceName": {
                                "referenceName": "ls_sqlmi_legacy",
                                "type": "LinkedServiceReference"
                            }
                        }
                    ]
                }
            }
        ],
        "annotations": [],
        "lastPublishTime": "2026-07-29T12:09:37Z"
    },
    "type": "Microsoft.DataFactory/factories/pipelines"
}
```

Structure: `Lookup2` generates a list of 50-row offsets spanning the Legacy table.
`ForEach1` iterates those offsets sequentially (`isSequential: true`). Each iteration runs
`Lookup1` (fetch 50 rows), `Web1` (POST to `/enrich`), and `Merge Back`
(`dbo.usp_merge_legacy_enriched` on linked service `ls_sqlmi_legacy`, taking the whole
response as a single string payload). Every activity has `retry: 0` and a 12-hour timeout.

**[AUTHOR]** This export predates the freeze. Before 2026-08-21 the pipeline is to be
amended to add a group-code predicate to both Lookups, an `enriched_at` watermark so
`Lookup1` selects only unenriched rows, and a retry policy above 0 on `Web1` and
`Merge Back`.

---

## 3 · ADF pipeline: Deduplication

**[EXPORT]** Published 2026-07-29T12:09:37Z. Reproduced verbatim.

```json
{
    "name": "Deduplication Pipeline",
    "properties": {
        "activities": [
            {
                "name": "Lookup1",
                "type": "Lookup",
                "dependsOn": [],
                "policy": {
                    "timeout": "0.12:00:00",
                    "retry": 0,
                    "retryIntervalInSeconds": 30,
                    "secureOutput": false,
                    "secureInput": false
                },
                "userProperties": [],
                "typeProperties": {
                    "source": {
                        "type": "SqlMISource",
                        "sqlReaderQuery": {
                            "value": "SELECT\n    Customer               AS row_id,\n    [Block ID]          AS block_id,\n    [Name 1]               AS name1,\n    [Name 2]               AS name2,\n    [Street 1]             AS street,\n    [House Number]         AS house_no,\n    [Postal Code]          AS postal_code,\n    City                   AS city,\n    [Country/Region Key]   AS country,\n    [ROR ID]               AS ror_id,\n    [LEI ID]               AS lei_id\nFROM test_77.Validation",
                            "type": "Expression"
                        },
                        "partitionOption": "None"
                    },
                    "dataset": {
                        "referenceName": "AzureSqlMITable3",
                        "type": "DatasetReference"
                    },
                    "firstRowOnly": false
                }
            },
            {
                "name": "Web1",
                "type": "WebActivity",
                "dependsOn": [
                    { "activity": "Lookup1", "dependencyConditions": ["Succeeded"] }
                ],
                "policy": {
                    "timeout": "0.12:00:00",
                    "retry": 0,
                    "retryIntervalInSeconds": 30,
                    "secureOutput": false,
                    "secureInput": false
                },
                "userProperties": [],
                "typeProperties": {
                    "method": "POST",
                    "headers": { "Content-Type": "application/json" },
                    "url": "https://mdm-pipeline-api.azurewebsites.net/api/dedup/cluster-block",
                    "connectVia": {
                        "referenceName": "AutoResolveIntegrationRuntime",
                        "type": "IntegrationRuntimeReference"
                    },
                    "body": {
                        "value": "@json(concat('{\"rows\":', string(activity('Lookup1').output.value), '}'))",
                        "type": "Expression"
                    }
                }
            },
            {
                "name": "Merge Back",
                "type": "SqlServerStoredProcedure",
                "dependsOn": [
                    { "activity": "Web1", "dependencyConditions": ["Succeeded"] }
                ],
                "policy": {
                    "timeout": "0.12:00:00",
                    "retry": 0,
                    "retryIntervalInSeconds": 30,
                    "secureOutput": false,
                    "secureInput": false
                },
                "userProperties": [],
                "typeProperties": {
                    "storedProcedureName": "dbo.usp_merge_validation_clusters",
                    "storedProcedureParameters": {
                        "payload": {
                            "value": {
                                "value": "@string(activity('Web1').output)",
                                "type": "Expression"
                            },
                            "type": "String"
                        }
                    }
                },
                "linkedServiceName": {
                    "referenceName": "ls_sqlmi_validation",
                    "type": "LinkedServiceReference"
                }
            }
        ],
        "annotations": [],
        "lastPublishTime": "2026-07-29T12:09:37Z"
    },
    "type": "Microsoft.DataFactory/factories/pipelines"
}
```

The projection read from `test_77.Validation` defines the dedup request contract:
`row_id` ← `Customer`, `block_id` ← `[Block ID]`, `name1` ← `[Name 1]`, `name2` ← `[Name 2]`,
`street` ← `[Street 1]`, `house_no` ← `[House Number]`, `postal_code` ← `[Postal Code]`,
`city` ← `City`, `country` ← `[Country/Region Key]`, `ror_id` ← `[ROR ID]`,
`lei_id` ← `[LEI ID]`. `[Block ID]` is precomputed by the DATAshaper address gate and read,
not derived, by the service.

**[AUTHOR]** Before 2026-08-21 this pipeline is to be amended to add a group-code predicate
and to iterate distinct `block_id` values through a ForEach rather than issuing one
unbatched Lookup over the whole Validation table.

---

## 4 · Stored procedures

**[EXPORT, partial]** Names and call sites are evidenced by the ADF exports above. Bodies
are not exported.

| Procedure | Called by | Parameter | Linked service |
|-----------|-----------|-----------|----------------|
| `dbo.usp_merge_legacy_enriched` | Enrichment Pipeline → Merge Back | `payload` (String) — full `/enrich` response | `ls_sqlmi_legacy` |
| `dbo.usp_merge_validation_clusters` | Deduplication Pipeline → Merge Back | `payload` (String) — full cluster response | `ls_sqlmi_validation` |

**[AUTHOR]** The stored procedures exist only to support ADF: fetching rows from tables and
merging responses back. They contain no enrichment or matching logic.

⚠ The procedure that writes the issues column back to Legacy is not evidenced in any export.
Required before the freeze.

---

## 5 · DATAshaper configuration

No file export is available; DATAshaper is a Tillit-hosted SaaS product configured through
its web interface. The following is **[OBSERVED]** from the interface.

### 5.1 Table progression

Import (bronze) → Legacy (silver) → Validation (gold) → load file. Mapping is configured
separately for Import→Legacy and Legacy→Validation. Mappings can be bound to a group code or
left universal; universal mappings apply to all group codes under the entity.

### 5.2 Processes

Each DS process corresponds to a SQL stored procedure. Processes are invoked either as ad-hoc
tasks in Batch processing or as `SqlServerStoredProcedure` activities from ADF, parameterised
by group code and entity. Observed task names: `LegacyMapping`, `MigrateData`,
`ProcessValidation`.

### 5.3 Validation rules

Validation rules are configured in DS against the Validation table (alias `W`). Rules may be
mandatory (an issue) or non-mandatory (a warning), may be bound to a group code or universal,
and may reference DP reference tables (for example US state codes). A rule can carry an
assigned responsible person. Rules read the issues column produced by the `/issues` endpoint,
and DS additionally applies its own rules independent of that column.

### 5.4 Issues view

Observed 2026-08-16. The view lists per-code counts under a collapsible `Issuelist`, with a
`Show mandatory only` filter, and drills from issue code to affected field to description.
Observed rendering:

```
G2-VAL-007  Search Term 1 Missing                     40
G1-ADDR-001 House Number Embedded in Street         3 / 8
G1-ADDR-003 Sub-location Embedded in Street         2 / 6
G1-ADDR-004 PO Box Embedded in Street                  1
   └ Street 1                                          1
      └ PO Box Embedded in Street                      1
G1-CROSS-001 Address Content in Name Field             1
G1-CROSS-003 Contact Information in Wrong Field        3
G1-NAME-013 SAP Internal Code in Name Field            1
```

The drill-down to field level means the issues column must encode the affected field, not
only the issue code. Offending cells are highlighted in the grid.

⚠ The exact serialisation of the issues column (delimiter, ordering, per-code field
encoding, value for a clean record) must be read from the code that builds it, not inferred
from this rendering.

### 5.5 Deduplication view

Observed 2026-08-16. Columns: `Cluster`, `Code`, `Reason`, `Cluster_ID`, `Block ID`,
`Signature`. One row per record; rows sharing a `Cluster_ID` form a cluster. `Reason` carries
the adjudicator's free-text justification, surfacing the model's reasoning to the steward.
Observed `Cluster_ID` values are opaque hashes prefixed `c_` (`c_22b1a6e41a78`,
`c_08af49644115`, `c_02b8fa9a8956`, `c_62204272ae47`, `c_3c18a74e1710`).

A side panel titled `Deduplication` shows the selected record's `Code`, a `Leading Code`
selector, an `Assign for` selector (observed value `Current record only`), and an
`Apply Leading Code` action. This is the human approval step: the system proposes, a steward
confirms.

⚠ `Block ID` and `Signature` rendered empty in the observed screenshot. Confirm whether this
is horizontal-scroll truncation or genuinely unpopulated.

### 5.6 Deployment

**[AUTHOR]** DATAshaper and Azure Data Factory run on the Tillit tenant. The Function App
`mdm-pipeline-api` and the AI Foundry deployment run on the Bruker Azure spoke. The Function
App is reached from ADF over the public endpoint
`https://mdm-pipeline-api.azurewebsites.net`.

---

## 6 · Production workflow

**[AUTHOR]** The implemented end-to-end workflow.

| # | Step | Executed by | Human in loop |
|---|------|-------------|---------------|
| 1 | Preprocess source file to processable schema; exclude ZFI records | script ⚠ artefact not located | yes |
| 2 | Create group code; import preprocessed file into DATAshaper | DS import | yes |
| 3 | Process legacy | ADF → DS stored procedure | no |
| 4 | Call `/enrich` reading from Legacy | ADF Enrichment Pipeline | no |
| 5 | Write enrichment results back to Legacy | `usp_merge_legacy_enriched` | no |
| 6 | Address validation; auto write-back above 80% confidence | ADF ⚠ pipeline not exported | no |
| 7 | Call `/issues`; write issues column back to Legacy | ADF ⚠ pipeline not exported | no |
| 8 | Process validation; DS rules read the issues column and apply their own | ADF → DS stored procedure | no |
| 9 | Review issues in the DS issues view; assign to a data steward | DS Studio | yes |
| 10 | Call `/api/dedup/cluster-block`; write clusters to Validation | ADF Deduplication Pipeline | no |
| 11 | Review clusters in the DS deduplication view | DS Studio | yes |
| 12 | Golden-record election proposes a leading code; a steward approves | scoring endpoint + DS Studio | yes |

`/issues` may also be run standalone against the raw file to produce a before-enrichment
issue baseline for evaluation. ⚠ Whether that path is in ADF or manual is unconfirmed.

**[AUTHOR]** ZFI records are excluded from processing on Bernd Schnurrer's instruction.
⚠ The rationale is not recorded in any artefact and must be supplied by the author.

---

## 7 · Open items before freeze

1. Stored procedure writing the issues column to Legacy — not evidenced.
2. ADF pipeline for address validation (step 6) — not exported.
3. ADF pipeline for the `/issues` call (step 7) — not exported.
4. Preprocessing and ZFI-exclusion script (step 1) — not located.
5. Whether ADF invokes `/api/dedup/score`, or whether election runs elsewhere.
6. Azure Functions hosting plan and its HTTP timeout ceiling.
7. Measured per-batch duration for a 50-row `/enrich` call.
8. `Block ID` and `Signature` population in the deduplication view.
