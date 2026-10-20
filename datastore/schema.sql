-- Apply once using the selected PostgreSQL platform's approved migration process.
-- CloudNativePG managed roles define snapshot_api and snapshot_collector; their
-- login credentials come from separate versioned ExternalSecrets. This migration
-- grants only the table operations required by each workload.
CREATE TABLE public.system_snapshots (
    run_id text PRIMARY KEY CHECK (char_length(run_id) BETWEEN 1 AND 253),
    sampled_at timestamptz NOT NULL,
    pod_name text NOT NULL,
    kernel_release text NOT NULL,
    architecture text NOT NULL,
    python_version text NOT NULL,
    logical_cpu_count integer NOT NULL CHECK (logical_cpu_count > 0),
    cpu_quota_millicores integer CHECK (cpu_quota_millicores > 0),
    memory_limit_bytes bigint CHECK (memory_limit_bytes > 0),
    memory_current_bytes bigint CHECK (memory_current_bytes >= 0),
    load_average_1m double precision CHECK (load_average_1m >= 0)
);

-- Replace these role names only if the selected platform's DBA policy requires
-- different names. Neither workload receives table ownership or schema DDL.
GRANT SELECT ON public.system_snapshots TO snapshot_api;
GRANT INSERT, DELETE ON public.system_snapshots TO snapshot_collector;
