# bspconv ( R5Flowstate/S21 )

Converts Respawn rBSP map files between versions. This fork targets the
Season 21 client: default output is **v51**.

Agents view included: CLAUDE.md

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

Upstream: [r-ex](https://github.com/r-ex) bspconv