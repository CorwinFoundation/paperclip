from concurrent.futures import ThreadPoolExecutor

from api.scan_log import ScanLog


def test_shared_memory_scan_log_serializes_concurrent_appends() -> None:
    thread_count = 32
    log = ScanLog(":memory:")

    def append(index: int) -> None:
        log.append_scan(
            agent_id=f"agent-{index}",
            run_timestamp=f"2026-07-19T08:00:{index:02d}Z",
            scan_id=f"scan-{index}",
            scanner_version="test",
            corpus_version="test",
            score_json={"score": index},
            forensic_bundle_uri=f"file:///scan-{index}.json",
        )

    try:
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            list(executor.map(append, range(thread_count)))

        persisted = sum(
            len(log.list_for_agent(f"agent-{index}"))
            for index in range(thread_count)
        )
        assert persisted == thread_count
    finally:
        log.close()
