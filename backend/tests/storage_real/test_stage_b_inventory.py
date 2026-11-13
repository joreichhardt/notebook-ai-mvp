"""Synthetic inventory protocol tests: never real Storage/SQL acceptance."""

import base64
import io
import json
import os
import subprocess
import tarfile
from hashlib import sha256, sha512
from pathlib import Path

import pytest

PREFIX = "STAGE_B_MIGRATION_INVENTORY="
SOURCE = json.loads(Path(__file__).with_name("stage_b_source.json").read_text())
ROWS = [(0, "INVENTORY_CANARY"), (79, "last-source-migration")]


def observed():
    return {
        "status": "observed",
        "actual_count": 2,
        "expected_count": SOURCE["migration_count"],
        "min_id": 0,
        "max_id": 79,
        "actual_fingerprint": sha256(json.dumps(ROWS, separators=(",", ":")).encode()).hexdigest(),
        "expected_fingerprint": SOURCE["migration_ids_names_sha256"],
    }


def test_actual_reporter_hashes_rows_without_outputting_names(capsys):
    from backend.tests.storage_real.stage_b import migration_inventory
    from backend.tests.storage_real.stage_b_launch import safe_line

    migration_inventory(ROWS, SOURCE)
    captured = capsys.readouterr()
    assert not captured.err and "INVENTORY_CANARY" not in captured.out
    assert captured.out == PREFIX + json.dumps(observed(), separators=(",", ":")) + "\n"
    assert safe_line(captured.out.rstrip("\n")) == {"migration_inventory": observed()}


@pytest.mark.parametrize(
    "rows,status",
    [
        ([], "empty"),
        (None, "malformed"),
        ([(True, "INVENTORY_CANARY")], "malformed"),
        ([(-1, "INVENTORY_CANARY")], "malformed"),
        ([(10001, "INVENTORY_CANARY")], "malformed"),
        ([(1, 42)], "malformed"),
        ([(1, "INVENTORY_CANARY", "extra")], "malformed"),
        ([(1, "INVENTORY_CANARY")] * 1001, "malformed"),
    ],
)
def test_actual_reporter_invalid_inventory_is_fixed_enum_only(capsys, rows, status):
    from backend.tests.storage_real.stage_b import migration_inventory

    with pytest.raises(AssertionError):
        migration_inventory(rows, SOURCE)
    assert (
        capsys.readouterr().out
        == PREFIX + json.dumps({"status": status}, separators=(",", ":")) + "\n"
    )


def test_actual_parser_accepts_only_closed_inventory_shape():
    from backend.tests.storage_real.stage_b_launch import safe_line

    assert safe_line(PREFIX + json.dumps(observed())) == {"migration_inventory": observed()}
    for status in ("empty", "malformed"):
        assert safe_line(PREFIX + json.dumps({"status": status})) == {
            "migration_inventory": {"status": status}
        }


@pytest.mark.parametrize(
    "key,value",
    [
        ("status", "INVENTORY_CANARY"),
        ("actual_count", True),
        ("actual_count", 0),
        ("actual_count", 1001),
        ("expected_count", True),
        ("expected_count", 1001),
        ("expected_count", 78),
        ("min_id", -1),
        ("min_id", False),
        ("min_id", 80),
        ("max_id", 10001),
        ("actual_fingerprint", "INVENTORY_CANARY"),
        ("expected_fingerprint", "0" * 64),
        ("names", ["INVENTORY_CANARY"]),
    ],
)
def test_actual_parser_rejects_canary_unknown_keys_and_unbounded_values(key, value):
    from backend.tests.storage_real.stage_b_launch import safe_line

    payload = observed()
    payload[key] = value
    assert safe_line(PREFIX + json.dumps(payload)) is None


def test_actual_lock_verified_source_loader_includes_bundled_zero_without_database():
    # Opt-in source research: no npm installation, SQL, sockets or child work by the loader.
    source_path = os.environ.get("A5_STAGE_B_STORAGE_SOURCE_ROOT")
    library_path = os.environ.get("A5_STAGE_B_MIGRATION_LIBRARY_SOURCE_ROOT")
    if not source_path or not library_path:
        pytest.skip("explicit immutable source caches required; never download/install in tests")
    source, library = Path(source_path), Path(library_path)
    lock_bytes = (source / "package-lock.json").read_bytes()
    assert (
        sha256(lock_bytes).hexdigest()
        == "6079b667bf7ee96f9b1c6b19a5c1611fc94154596090681314eb7ffbb1600117"
    )
    lock = json.loads(lock_bytes)["packages"]["node_modules/postgres-migrations"]
    assert lock["version"] == "5.3.0"
    archive = (library / "source.tgz").read_bytes()
    assert "sha512-" + base64.b64encode(sha512(archive).digest()).decode() == lock["integrity"]
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar:
            path = Path(member.name)
            assert (
                not path.is_absolute()
                and ".." not in path.parts
                and not member.issym()
                and not member.islnk()
            )
            if member.isfile():
                cached = library / path
                assert cached.is_file() and not cached.is_symlink()
                stream = tar.extractfile(member)
                assert stream is not None
                assert sha256(cached.read_bytes()).digest() == sha256(stream.read()).digest()
    directory = source / "migrations/tenant"
    sql = sorted(directory.iterdir())
    assert len(sql) == 79 and all(path.suffix == ".sql" and not path.is_symlink() for path in sql)
    catalog = [[path.name, sha256(path.read_bytes()).hexdigest()] for path in sql]
    assert (
        sha256(json.dumps(catalog, separators=(",", ":")).encode()).hexdigest()
        == "1bee4c6295697de9c96c1c5b022bcfc9072980fc3b1ca707122e2522ab6239db"
    )
    program = r"""
const crypto=require('node:crypto');
let forbidden=0;
const deny=()=>{forbidden++;throw new Error('NETWORK_CHILD_FORBIDDEN')};
const net=require('node:net'),dns=require('node:dns'),child=require('node:child_process');
net.connect=net.createConnection=net.Socket.prototype.connect=deny;
for(const name of ['lookup','resolve']){dns[name]=deny;dns.promises[name]=deny;}
for(const name of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork'])child[name]=deny;
const {loadMigrationFiles}=require(process.argv[1]);
loadMigrationFiles(process.argv[2]).then(rows=>{
 const pairs=rows.map(r=>[r.id,r.name]);
 const positive=pairs.filter(p=>p[0]>0);
 const hash=p=>crypto.createHash('sha256').update(JSON.stringify(p)).digest('hex');
 console.log(JSON.stringify({count:pairs.length,min_id:Math.min(...pairs.map(p=>p[0])),max_id:Math.max(...pairs.map(p=>p[0])),fingerprint:hash(pairs),positive_count:positive.length,positive_fingerprint:hash(positive),bootstrap_zero:pairs.filter(p=>p[0]===0&&p[1]==='create-migrations-table').length===1,forbidden_attempts:forbidden}));
}).catch(()=>{console.log('SOURCE_LOADER_FAILED');process.exitCode=1});
"""
    result = subprocess.run(
        ["node", "-e", program, str(library / "package/dist/files-loader.js"), str(directory)],
        capture_output=True,
        timeout=10,
        env={"PATH": os.environ["PATH"]},
        check=True,
    )
    assert not result.stderr
    actual = json.loads(result.stdout)
    assert (actual["count"], actual["fingerprint"]) == (
        SOURCE["migration_count"],
        SOURCE["migration_ids_names_sha256"],
    ), "manifest_does_not_match_actual_integrity_checked_loader"
    assert actual == {
        "count": len(sql) + 1,
        "min_id": 0,
        "max_id": 79,
        "fingerprint": "a679dd223ed23d35f0e3b859b70dd73923033bf0cacfa8daa431fc8f4cc5ecb8",
        "positive_count": len(sql),
        "positive_fingerprint": "bdc5142fcb1ec6de74224115cec0c944c4bc36460bfea652f3da28494d314185",
        "bootstrap_zero": True,
        "forbidden_attempts": 0,
    }
