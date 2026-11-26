# Final audit and validation

Date: 2026-11-26. Scope: the Python web application, SQL workspace, English interface, original import workflow and deployment configuration in this repository.

## Verified results

| Check | Result |
| --- | --- |
| Automated suite with `NETACT_TEST_DATABASES=1` | **55 passed, 0 skipped** |
| Real database integration | MariaDB 10.11 (native connector), MySQL 8.4, PostgreSQL 17 |
| Original conversion workflow | Bundled JAR conversion, CSV import, continued processing of rejected rows/tables |
| SQL execution | Multiple statements, procedure result sets, PostgreSQL dollar blocks, ordinary DML rollback |
| SQL CSV exports | Complete captured results, including rows beyond the preview; repeated exports do not repeat writes |
| Result lifecycle | Expiry, active download leases, normal shutdown and failed-send resource cleanup |
| Browser validation | Chromium: table paging/downloads, UTF-8 SQL file, script execution, result selection/downloads, keyboard shortcut, error and disconnect paths |
| Responsive layout | No horizontal page overflow at 360, 390, 768, 1024 and 1440 px |
| Docker smoke test | Final image built; health/Java check and native MariaDB CREATE/INSERT/SELECT, snapshot export and cleanup passed |
| Python static checks | Ruff lint and formatting passed |
| Dependency consistency | `pip check` passed |
| Installed Python dependency audit | `pip-audit`: **no known vulnerabilities found**, no ignored advisories |

The dependency audit used pip-audit 2.10.1 against the application virtual environment. Vulnerable baseline Python packages were upgraded; the tested versions are recorded in `requirements.lock`. Advisory results reflect the database available on the audit date and should be checked again during future updates.

One nonblocking warning remains in the test tooling: the upstream Starlette TestClient deprecates its current httpx integration. All 55 tests passed with the pinned test dependencies.

## Changes reviewed

- SQL runs once on a fresh connection. Row results are saved to private, short-lived CSV files; export reads those files rather than submitting the SQL again.
- Script failures stop remaining statements and attempt rollback. Error responses omit SQL text, parameters and raw driver messages.
- Snapshot tokens are random; paths are resolved from server records. Expired results are removed, and active downloads hold a lease. Resources are released even when sending a response fails.
- Shared database connection/setup, import job routing, CSV serialization and browser utilities replace duplicated code. MariaDB retains the native connector used by the original importer.
- Saved-password binding, database creation behavior, quoted identifiers and per-row/table import recovery remain covered by tests.
- Mutation requests with a different Origin are rejected. API responses disable caching, and responses set `X-Content-Type-Options: nosniff`. These controls do not provide authentication.
- Interface labels and runtime messages are English. Data, including Vietnamese text and HTML-like cell values, remains unchanged and is rendered as text.

## Remaining limits

- **Access:** there is no built-in login or per-user authorization. Run on localhost or behind an authenticated private gateway. Anyone with application access can use server-saved connection profiles. SQL runs with the connected database account's permissions.
- **Transactions:** DDL, explicit COMMIT, nontransactional tables and database-specific operations may commit before a later error. The console cannot guarantee that an arbitrary script is atomic.
- **Long operations:** there is no query cancellation or statement timeout. Losing the HTTP connection does not guarantee cancellation; verify database state before retrying writes. Use one application worker.
- **Resources:** there is no application XML upload cap. Available disk/RAM, Java, database settings and reverse proxies still impose practical limits. PostgreSQL DML RETURNING and other non-SELECT results can buffer in driver memory. Large SQL files occupy editor/parser memory.
- **Temporary data:** SQL snapshots contain exported data. Normal shutdown removes them; a process/OS crash can leave temporary files. Protect the host, temporary storage and access logs containing snapshot tokens.
- **CSV:** exports preserve cell values. Spreadsheet applications may interpret formula-like values when opening a CSV; choose suitable spreadsheet import settings for untrusted data.
- **Coverage:** testing used synthetic databases and fixtures, not the user's production server or complete multi-gigabyte XML. Windows installation is documented but was not exercised on a live Windows host in this environment.
- **Audit scope:** this is a code/dependency review and functional validation, not a penetration test or a full operating-system/container/JAR vulnerability audit. The supplied parser JAR was retained unchanged.

See [README_EN.md](README_EN.md) for operation, setup and recovery details.
