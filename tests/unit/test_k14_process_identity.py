from k14_process_probe import linux_processes, local_postgres_root


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


def test_linux_sampling_keeps_fractional_cpu_and_never_reads_command_arguments(tmp_path, monkeypatch):
    monkeypatch.setattr('k14_process_probe.os.sysconf', lambda name: 100 if name == 'SC_CLK_TCK' else 4096, raising=False)
    process = tmp_path / '123'
    process.mkdir()
    fields = ['0'] * 22
    fields[0], fields[1], fields[11], fields[12], fields[21] = 'S', '7', '20', '5', '2'
    (process / 'stat').write_text('123 (worker name) ' + ' '.join(fields))
    (process / 'fd').mkdir()
    (process / 'fd' / '0').touch()
    assert linux_processes(tmp_path) == {123: {'parent': 7, 'rss_bytes': 8192, 'cpu_seconds': .25, 'open_fds': 1}}


def test_disappeared_process_is_absent_and_unavailable_fd_is_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr('k14_process_probe.os.sysconf', lambda name: 100 if name == 'SC_CLK_TCK' else 4096, raising=False)
    (tmp_path / '124').mkdir()
    process = tmp_path / '125'
    process.mkdir()
    fields = ['0'] * 22
    fields[0] = 'S'
    (process / 'stat').write_text('125 (worker) ' + ' '.join(fields))
    rows = linux_processes(tmp_path)
    assert 124 not in rows and rows[125]['open_fds'] is None
