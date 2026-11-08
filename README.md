# A pack template

A starting point for a story the Lethebar engine can install and play. Click
**Use this template** on GitHub, rename the repository, and replace the example
pack with yours; nothing else has to be assembled by hand.

## What is here

```
packs/example/          one small, working pack: a manifest and three nodes
infra/tool/release/     build, hash and publish a pack
infra/checks/           the archive rules the engine also applies on install
.github/workflows/      build -> check -> publish, on every push and tag
```

## The six commands

```bash
git clone https://github.com/<you>/<your-repo>.git && cd <your-repo>
python infra/tool/release/pack.py build packs/example --out dist
python infra/checks/check_pack.py dist/example.zip
python infra/tool/release/pack.py hash dist/example.zip
git tag pack/example/v0.1.0 && git push origin pack/example/v0.1.0
```

The sixth is the tag push: it is the only thing that publishes. A release ends
up with two assets, `example.zip` and `example-0.1.0.json`, and a player installs
the zip with "Install a pack (.zip)".

## The versions

Three numbers, three jobs:

- the pack's own `version` in `manifest.json`; the tag must carry the same string
- `specVersion`, the pack format, `2.0.0` today
- `engine`, the range of engines the pack claims to run on

The repository is tested against engine **v0.6.1**. An engine newer than that is
allowed by the range in the manifest; an older one usually is not.

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