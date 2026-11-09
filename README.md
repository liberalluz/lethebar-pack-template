# A pack template

A starting point for a story the Lethebar engine can install and play. Click
**Use this template** on GitHub, rename the repository, and replace the example
pack with yours; nothing else has to be assembled by hand.

<!-- pack-badges:begin -->
![packs](https://img.shields.io/badge/packs-1-6f42c1)
![spec](https://img.shields.io/badge/spec-2.0.0-007ec6)
![engine](https://img.shields.io/badge/engine-%3E%3D4.7.0%20%3C5.0.0-478cbf)

| Kind | Pack | Version | Spec | Engine |
| --- | --- | --- | --- | --- |
| `bundle` | `example` | `0.1.0` | `2.0.0` | `>=4.7.0 <5.0.0` |
<!-- pack-badges:end -->

## What is here

```
packs/example/          one small, working pack: a manifest and three nodes
.github/workflows/      build -> check -> publish, on every push and tag
```

The tools are not copied into this repository. `gate.yml` checks the engine out
at the tag in `ENGINE_REF` and runs the engine's own `pack.py` and
`check_pack.py`, so the rules that judge a pack are always the rules of the
engine release named there. Nothing here can drift from a stale copy.

## The commands

Run the same commands locally with the engine checked out beside this
repository:

```bash
git clone https://github.com/<you>/<your-repo>.git && cd <your-repo>
git clone https://github.com/liberalluz/lethebar.git ../lethebar   # once
python ../lethebar/infra/tool/release/pack.py build packs/example --out dist
python ../lethebar/infra/checks/check_pack.py dist/example.zip
python ../lethebar/infra/tool/release/pack.py hash dist/example.zip
git tag pack/example/v0.1.0 && git push origin pack/example/v0.1.0
```

The tag push is the only thing that publishes. A release ends up with two
assets, `example.zip` and `example-0.1.0.json`, and a player installs the zip
with "Install a pack (.zip)".

## The versions

Three numbers, three jobs:

- the pack's own `version` in `manifest.json`; the tag must carry the same string
- `specVersion`, the pack format, `2.0.0` today
- `engine`, the range of Godot builds the pack claims to run on, such as
  `>=4.7.0 <5.0.0`

`ENGINE_REF` in `gate.yml` is a fourth number, and a different one: it names the
Lethebar release whose tools check this repository, **v0.7.2** today. Bump that
line when you want a newer checker. It is not the `engine` field: the field is
about the runtime, `ENGINE_REF` is about the checker.

## The README table

The badges and the table above are written from `packs/*/manifest.json`; do not
edit them by hand:

```bash
python ../lethebar/infra/tool/release/pack.py badge . --write README.md
python ../lethebar/infra/tool/release/pack.py badge . --check README.md
```

`--check` names the pack and the field that disagrees, so a version bump that
skips the command has a way to fail. One repository, one table: the `Kind` column
says which rows are whole games and which are layers.

## What the gate checks

`build` writes a reproducible zip. `check_pack` reads it back and refuses a
stray file at the archive root, a path that escapes the archive, an executable,
more than 512 entries, a file over 8 MiB or an archive over 32 MiB. The engine
applies the same rules again when a player installs the pack, so a green gate
here means the install will not be refused for shape.

## License

Apache-2.0, the same as the engine.

## Adding a second pack

A world is one repository with several packs, side by side:

```
packs/example/          the pack this template ships
packs/my-story/         yours: manifest.json and data/
packs/my-rules/         a layer over the first one
```

Each pack keeps its own id, its own version and its own tag. To publish the
second one, tag it and nothing else is touched:

```bash
git tag pack/my-story/v0.1.0 && git push origin pack/my-story/v0.1.0
```

The gate reads the id out of the tag, builds `packs/my-story`, and refuses the
push if that folder has no `manifest.json`. Two packs must never share an id:
the id is the save key, so a duplicate would read another story save.

A layer declares what it sits on:

```json
{ "kind": "layer", "layer": "mod", "target": "example", "requires": ["example"] }
```

See the engine's "one repository, several packs" page for the full rules.
