# bspconv — agent notes

Human overview: `README.md`. This file is the CLI and gotchas.

Default target is **rBSP v51** (S21 client).

## Build

`bspconv.sln`, Release x64, toolset **v145** if MSB8020.

```
MSBuild.exe bspconv.sln -p:Configuration=Release -p:Platform=x64 -p:PlatformToolset=v145
```

Output: `x64/Release/bspconv.exe`. Diagnostic scripts in `tools/`.

## CLI

```
bspconv <input.bsp> [-pack] [-dedi] [-out <dir>]
```

- Input is the **split header**. Sidecars `<map>.bsp.<xxxx>.bsp_lump` and
  `_*.ent` must sit next to it. Version is read from the header.
- Omit `-dedi` → **v51 / flags=1** (S21 client).
- `-dedi` → **v47 / flags=0** (S3 dedicated server): server-only lump strip +
  ENTITIES / partition brush collision v121 → v8. sprp GAME_LUMP version is
  set to `0x33` and is **cosmetic** (engine keys `'sprp'` + `filelen`).
- `-pack` → one monolithic `.bsp`.
- `-out D/` → copy then convert (source untouched). No `-out` = **in place**.

## Do

- After RSX extract, **strip `.client` / `.server`** from lump names before
  this tool (it wants `.bsp.<xxxx>.bsp_lump`).
- Sidecar hex **must be lowercase**. Engine opens `%.4x` then the FS
  lowercases; VPK lookup is case-sensitive. Uppercase `.006A` misses and the
  loader reads the rBSP header as the lump. The converter normalizes A–F at
  start; still assert no uppercase hex after extract.
- Confirm output against a known-good v47/v51 map of the same role, not a
  stale deploy.

## `-dedi` lump union

Server lump set is a **union across maps**, not one map's list. A too-narrow
whitelist silently drops vis/collision a map needs. New map that needs a
missing lump: add it to the union in `IsServerLump`, do not invent a per-map
table.

Kept (25):

| idx | lump |
|-----|------|
| `0x00` | ENTITIES |
| `0x01` | PLANES |
| `0x02` | TEXDATA |
| `0x03` | VERTEXES |
| `0x0e` | MODELS |
| `0x0f` | TEXDATA_STRING_DATA |
| `0x10` | CONTENTS_MASKS |
| `0x11` | SURFACE_PROPERTIES |
| `0x12` | BVH_NODES |
| `0x13` | BVH_LEAF_DATA |
| `0x14` | PACKED_VERTICES |
| `0x18` | ENTITY_PARTITIONS |
| `0x23` | GAME_LUMP (static props) |
| `0x25` `0x27` | VIS |
| `0x36` | WORLD_LIGHTS |
| `0x50` | MESHES |
| `0x52` | MATERIAL_SORT |
| `0x55` | TWEAK_LIGHTS |
| `0x6a` | CELL_BSP_NODES |
| `0x6b` | CELLS |
| `0x77` | CELL_AABB_NODES |
| `0x78` | OBJ_REFS |
| `0x79` | OBJ_REF_BOUNDS |
| `0x7b` | LEVEL_INFO |

Those extras are disjoint across maps (one map needs `0x02/0x36/0x50/0x52/0x55/0x6a`,
another needs `0x14/0x25/0x27/0x6b/0x77/0x78/0x79`) — hence the union.

Dropped: lightprobes (`0x65`), lightmaps (`0x53/0x61/0x62/0x69/0x7A`), render
verts (`0x47`–`0x4e`), mesh indices (`0x4f`), portals (`0x6c`–`0x74`),
occlusion/shadow mesh (`0x75/0x76/0x7c`–`0x7f`), CSM, cubemaps, v52-only slots.

## Stubs

- TF2 v37 path stages through v47; v51 post-bump is incomplete. No BVH gen.
- Packed-mode emit of lump `0x69` over-reads; split-lump path is the one that
  works. `[RTL] STUB` prints if `-pack` hits it.
- A stripped/zeroed `0x18` ENTITY_PARTITIONS names header makes the v121→v8
  partition rewrite a no-op. Start from a fresh RSX extract.

## Do not

- Feed `_server_perm` / `_server_temp` wraps into a dedi convert. Use the
  **client** wrap set (cell/obj-ref vis). Newer server vis is a different
  system.
- “Fix” lump `0x69` header vs on-disk size (padded in-memory vs unpadded
  file). Preserve the header value.
- Treat sprp `0x33` vs `0x34` as a struct-layout bug. It is not.
- Run the v51 client path’s v121→v8 downgrade on data that is already v8
  (TF2-sourced maps). `-dedi` assumes v121 when source >= 48.

## Versions

| ver | flags | role |
|-----|-------|------|
| 37 | 0 | Titanfall 2 (upgrade path; no BVH gen) |
| 47 | 0 | S3 dedi (`-dedi`) |
| 51 | 1 | S21 client (default) |
| 52 | 1 | newer client; downgrade to 51 or 47 |
