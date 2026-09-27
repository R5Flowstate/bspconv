# bspconv (R5Flowstate / S21)

Converts Respawn rBSP map files between versions. This fork is the S21-bridge
converter: default output is **v51** (Season 21 client). Upstream / original
bspconv targeted older r5reloaded (v47) only.

Upstream: [r-ex](https://github.com/r-ex) bspconv lineage.

## What this fork adds

- **Default target is v51** (S21 client, `flags=1`), not v47.
- **`-dedi`** selects v47 for the S3 dedicated server (server-only lump strip,
  ENTITIES brush-model downgrade, sprp gamelump version kept at `0x33`).
- v52 (and nearby) **downgrade to v51**: header, GAME_LUMP version, lightmap
  header stride, v52-only lump clear.
- Lightprobe size contract when up-converting older maps to v51 (48B -> 44B).
- Lowercase sidecar names so the S3 VPK lookup does not miss lumps.

The built-in Titanfall 2 (v37) upgrade generates no collision. Use the
`tools/` pipeline below for Titanfall 2 maps.

## Titanfall 2 map pipeline (`tools/`)

Python 3 scripts (numpy; capstone and pefile for the DLL readers) that turn a
Titanfall 2 map into an S21 client map and an S3 dedicated-server map:

- `tf2coll/` - BSP conversion (v37 -> v51 client, v47 dedi), collision rebuilt
  as Apex BVH4, real-time light lightmaps, brush-entity and trigger collision,
  entity partitions, cubemaps, client wrap paks, and offline validators.
- `tf2mat/` - TF2 materials rebuilt as S21 materials on their twin shader sets,
  VMT-only materials, and the client/dedi Repak manifests.
- `tf2fx/` - TF2 particle systems (`.pcf`) -> S21 effects (efct v16), their
  particle materials, and colour correction baked to `.raw_hdr`.
- `tf2common/` - shared helpers (asset GUID hash, pak reader, tool paths).

External tools (RePak, RSX, a shader disassembler) are found on `PATH`, or set in
`tools/tf2common/paths.json` (copy `paths.example.json`) or the `TF2_REPAK_EXE`,
`TF2_RSX_EXE` and `TF2_SHADER_DISASM` environment variables. Each script's
docstring gives its usage.

## Usage

```
bspconv <input.bsp> [-pack] [-dedi] [-out <outputDir>]
```

```
# S21 client (v51) -- default
bspconv map.bsp

# pack lumps into one BSP
bspconv map.bsp -pack

# S3 dedicated server (v47)
bspconv map.bsp -dedi
```

## Building

Open `bspconv.sln` in Visual Studio and build x64 Release.
