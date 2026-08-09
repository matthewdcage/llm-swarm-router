# Golden config generations

One directory per migration step: `v<from>-to-v<to>/before.toml` and
`after.toml`. `tests/test_config_migrations.py` parses `before.toml`, runs the
migration chain, and asserts the result equals the parsed `after.toml` —
exactly, key for key.

Golden **pairs**, not golden output: the test compares two files a human wrote
and reviewed. Recording `after.toml` from the code under test would make the
fixture agree with whatever the code does, which is the failure mode
`NETLLM_VECTOR_RECORD` is fenced against elsewhere in this repo (PROGRAM.md
§9 R5). If you change a migration, hand-edit `after.toml` and say why in the
commit.

Each pair is deliberately awkward: unknown sections, unknown keys inside known
sections, an unknown cloud provider, and a deprecated key. A migration that is
correct only on a tidy config is not correct.
