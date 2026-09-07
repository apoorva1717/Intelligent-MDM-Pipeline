CREATE PROCEDURE [Mapping].[usp_MergeLegacyIssues]
    @chrEntity SYSNAME,
    @chrGroupCode NVARCHAR(50),
    @payload NVARCHAR(MAX),
    @target_column SYSNAME = N'Issues'
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @msg NVARCHAR(400);

    ------------------------------------------------------------------
    -- Guard 0: target column is one of the two allowed
    ------------------------------------------------------------------
    IF @target_column NOT IN (N'Issues Before', N'Issues')
    BEGIN
        THROW 50000, N'target_column must be Issues Before or Issues.', 1;
    END;

    ------------------------------------------------------------------
    -- Guard 1: entity must exist as a schema in dp_legacy
    ------------------------------------------------------------------
    IF NOT EXISTS (SELECT 1 FROM dp_legacy.sys.schemas WHERE name = @chrEntity)
    BEGIN
        SET @msg = N'Entity schema ' + @chrEntity + N' does not exist in dp_legacy.';
        THROW 50001, @msg, 1;
    END;

    ------------------------------------------------------------------
    -- Guard 2: group code must be supplied and exist in the entity Legacy
    ------------------------------------------------------------------
    IF NULLIF(LTRIM(RTRIM(@chrGroupCode)), N'') IS NULL
    BEGIN
        THROW 50002, N'Group code is required.', 1;
    END;
    -- Group code is scoped by the code prefix: <groupcode>_<source key>
    DECLARE @pat NVARCHAR(60) = LTRIM(RTRIM(@chrGroupCode)) + N'\_%';
    DECLARE @esc NCHAR(1) = N'\';
    DECLARE @tgt NVARCHAR(300) = N'dp_legacy.' + QUOTENAME(@chrEntity) + N'.Legacy';
    DECLARE @cnt INT;
    DECLARE @chk NVARCHAR(MAX) = N'SELECT @c = COUNT(*) FROM ' + @tgt + N' WHERE [code] LIKE @pat ESCAPE @esc;';
    EXEC sp_executesql @chk, N'@pat NVARCHAR(60), @esc NCHAR(1), @c INT OUTPUT', @pat = @pat, @esc = @esc, @c = @cnt OUTPUT;
    IF @cnt = 0
    BEGIN
        SET @msg = N'Group code ' + @chrGroupCode + N' has no rows in entity ' + @chrEntity + N'.';
        THROW 50003, @msg, 1;
    END;

    ------------------------------------------------------------------
    -- Static SQL: flatten each record's issues array to "; "-joined text
    ------------------------------------------------------------------
    IF OBJECT_ID(N'tempdb..#src') IS NOT NULL DROP TABLE #src;
    SELECT j.[record_id],
    ISNULL(
        STRING_AGG(x.[value], N'; ') WITHIN GROUP (ORDER BY CAST(x.[key] AS INT)),
        N'') AS [issues_csv]
    INTO #src
    FROM OPENJSON(@payload, N'$.results')
    WITH (
        [record_id] NVARCHAR(100) N'$."record_id"',
        [issues] NVARCHAR(MAX) N'$."issues"' AS JSON
    ) AS j OUTER APPLY OPENJSON(j.[issues]) AS x GROUP BY j.[record_id];

    ------------------------------------------------------------------
    -- Dynamic SQL: schema and target column are the only spliced parts
    ------------------------------------------------------------------
    DECLARE @sql NVARCHAR(MAX) = N'      MERGE ' + @tgt + N' AS tgt      USING #src AS src         ON tgt.[Customer] = src.[record_id]        AND tgt.[code] LIKE @pat ESCAPE @esc      WHEN MATCHED THEN UPDATE SET          tgt.' + QUOTENAME(@target_column) + N' = src.[issues_csv];';
    EXEC sp_executesql @sql, N'@pat NVARCHAR(60), @esc NCHAR(1)', @pat = @pat, @esc = @esc;
    DROP TABLE #src;
END;
