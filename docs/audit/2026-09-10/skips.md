# macOS baseline skips (Python 3.12)

Python 3.11 has the same 22 skipped cases.

| Test | pytest skip reason |
|---|---|
| `tests.agent.test_deep_loop_unit::test_bash_cache_keyed_by_shell_config` | bash resolution cache is Windows-only |
| `tests.agent.test_perf_baseline::test_perf_qa_no_tools` | requires CODERIO_PERF_TESTS=1 + API key + deepagents |
| `tests.agent.test_perf_baseline::test_perf_analysis_single_read` | requires CODERIO_PERF_TESTS=1 + API key + deepagents |
| `tests.session.test_store::test_windows_lock_mutex_survives_file_growth` | P0-5 regression is Windows msvcrt-specific; POSIX flock locks the whole file |
| `tests.session.test_store::test_created_session_file_owner_only` | Windows ACL semantics; POSIX asserts 0600 directly |
| `tests.test_seams::test_seamC_env_reaches_run_sandboxed_on_windows` | Windows-only seam test — env forwarding not yet implemented on Win32 |
| `tests.tools.test_grep_tool::test_python_fallback_used_when_no_rg` | rg is installed — Python fallback not exercised |
| `tests.tools.test_sandbox::test_create_write_restricted_token_returns_handle_or_none` | Windows-only Restricted Token |
| `tests.tools.test_sandbox::test_run_sandboxed_echo_succeeds` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_run_sandboxed_truncates_large_output` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_run_sandboxed_timeout_kills_process_quickly` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_run_sandboxed_timeout_kills_grandchild_process` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_finalize_run_degrades_on_init_failure_ntstatus` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_finalize_run_keeps_real_nonzero_exit` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_run_plain_fallback_runs_and_marks` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_run_plain_fallback_preserves_quoting_for_multitoken_commands` | Windows-only sandbox path |
| `tests.tools.test_sandbox::test_run_plain_fallback_truncates_multitoken_output` | Windows-only sandbox path |
| `tests.tools.test_win_job::test_create_job_with_limits_returns_handle_on_windows` | Windows-only ctypes path |
| `tests.tools.test_win_job::test_create_job_no_limits_still_works` | Windows-only ctypes path |
| `tests.tools.test_win_job::test_kill_process_tree_windows_terminates_process` | Windows-only ctypes path |
| `tests.tools.test_win_job::test_kill_process_tree_uses_taskkill_tree_flag` | taskkill is Windows-only |
| `tests.tools.test_win_job::test_kill_process_tree_kills_grandchildren` | Windows-only tree kill |
