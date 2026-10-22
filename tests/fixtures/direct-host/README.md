# Direct host v1 fixtures

These fixtures freeze the versioned headless host records used before desktop
consumers are wired. Successful records are one strict UTF-8 JSON document with
one final LF. Limits are 1 MiB per inventory record, 64 KiB per entry
reference, 16 KiB per string, 128 targets, 2 provider coverage records and 256
structured errors. Unknown schema versions, duplicate JSON object keys,
required-field omissions, type mismatches and oversized records are rejected.

Unknown bounded diagnostic fields are accepted. Entry references reject fields
that could supply labels, executable paths, commands, credentials or daemon
process identity; the helper always resolves those from local state.
