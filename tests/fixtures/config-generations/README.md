# Golden config generations

One directory per migration step: `v<from>-to-v<to>/before.toml` and
`after.toml`. `tests/test_config_migrations.py::test_every_migration_step_has_a_golden_pair`
parses `before.toml`, runs **that one step** (stamped the way the runner
stamps it), and asserts the result equals the parsed `after.toml` — exactly,
key for key. A step with no directory here fails that test.

Per step, not per chain: `migrate_document` runs every pending migration, so
once there was more than one step, running the chain on `v1-to-v2/before.toml`
produced a generation-3 document. Comparing that to `v1-to-v2/after.toml`
would force one pair to describe two steps and stop being reviewable by eye.

Golden **pairs**, not golden output: the test compares two files a human wrote
and reviewed. Recording `after.toml` from the code under test would make the
fixture agree with whatever the code does, which is the failure mode
`NETLLM_VECTOR_RECORD` is fenced against elsewhere in this repo (PROGRAM.md
§9 R5). If you change a migration, hand-edit `after.toml` and say why in the
commit.

Each pair is deliberately awkward: unknown sections, unknown keys inside known
sections, an unknown cloud provider, and a deprecated key. A migration that is
correct only on a tidy config is not correct.
