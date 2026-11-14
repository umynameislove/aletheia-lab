from k14_process_probe import local_postgres_root


def test_container_or_remote_backend_is_not_a_host_process(tmp_path):
    assert local_postgres_root(1976, str(tmp_path), {1976: {'parent': 1}, 1: {'parent': 0}}) is None


def test_missing_backend_pid_is_reported_unavailable(tmp_path):
    (tmp_path / 'postmaster.pid').write_text('101\n')
    assert local_postgres_root(1976, str(tmp_path), {101: {'parent': 1}}) is None


def test_local_pid_file_and_live_backend_lineage_bind_the_server(tmp_path):
    (tmp_path / 'postmaster.pid').write_text('101\n')
    assert local_postgres_root(1976, str(tmp_path), {101: {'parent': 1}, 1976: {'parent': 101}}) == 101


def test_reused_or_malformed_pid_does_not_supply_database_metrics(tmp_path):
    (tmp_path / 'postmaster.pid').write_text('101\n')
    assert local_postgres_root(1976, str(tmp_path), {101: {'parent': 1}, 1976: {'parent': 90}}) is None
    (tmp_path / 'postmaster.pid').write_text('invalid\n')
    assert local_postgres_root(1976, str(tmp_path), {}) is None
