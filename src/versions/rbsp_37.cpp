#include "stdafx.h"
#include "versions.h"
#include "rmem.h"
#include "bspfile.h"
#include "tf2_structs.h"
#include "binstream.h"
#include "stltools.h"

// ============================================================================
// Titanfall 2 (v37) -> Apex Legends (v47) BSP Conversion
// ============================================================================

// Forward declarations
static void ConvertTextureData_TF2(const char* srcData, size_t srcSize,
                                    const char* stringTableData, size_t stringTableSize,
                                    char*& dstData, size_t& dstSize);
static void ConvertModels_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertMaterialSorts_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertLevelInfo_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertMeshes_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertVertexUnlit_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertVertexLitBump_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertVertexLitFlat_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertVertexUnlitTS_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void ConvertVertexBlinnPhong_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize);
static void GenerateEntityPartitions_TF2(const char* entitiesData, size_t entitiesSize, char*& dstData, size_t& dstSize);
static void GenerateSurfaceNames_TF2(const char* stringData, size_t stringDataSize, char*& dstData, size_t& dstSize);
static void GenerateEmptyContentsMasks(char*& dstData, size_t& dstSize);
static void GenerateEmptySurfaceProperties(char*& dstData, size_t& dstSize);
static void GenerateEmptyPackedVertices(char*& dstData, size_t& dstSize);
static void WriteLumpExternal(const std::string& basePath, int lumpIndex, const char* data, size_t size);

// Apex v47 target structures (for reference)
#pragma pack(push, 1)
struct apex_dtexdata_t
{
    int32_t nameIndex;      // Direct byte offset into SURFACE_NAMES
    int32_t width;
    int32_t height;
    int32_t flags;
};
static_assert(sizeof(apex_dtexdata_t) == 16, "apex_dtexdata_t must be 16 bytes");

// Forward declarations for functions that need apex_dtexdata_t
static void GenerateContentsMasks(const std::vector<apex_dtexdata_t>& texdata, char*& dstData, size_t& dstSize);
static void GenerateSurfaceProperties(const std::vector<apex_dtexdata_t>& texdata, char*& dstData, size_t& dstSize);

struct apex_dmodel_t
{
    Vector mins;
    Vector maxs;
    int32_t firstMesh;
    int32_t meshCount;
    int32_t bvhNode;            // -1 for no BVH
    int32_t bvhLeaf;            // -1 for no BVH
    int32_t firstVertex;        // 0
    int32_t vertexFlags;        // 0
    Vector packedVertexOffset;  // (0,0,0)
    float nodeScale;            // 1.0
};
static_assert(sizeof(apex_dmodel_t) == 64, "apex_dmodel_t must be 64 bytes");

struct apex_dmaterialsort_t
{
    int16_t texdata;
    int16_t lightmapHeader;
    int16_t unknown[2];         // Padding/unknown
    int32_t vertexOffset;
};
static_assert(sizeof(apex_dmaterialsort_t) == 12, "apex_dmaterialsort_t must be 12 bytes");

struct apex_dlevelinfo_t
{
    int32_t unknown[4];         // Mesh indices from TF2 + padding
    int32_t numStaticProps;
    Vector sunNormal;
    int32_t numEntityModels;
};
static_assert(sizeof(apex_dlevelinfo_t) == 36, "apex_dlevelinfo_t must be 36 bytes");

// Apex v47 VertexLitBump: 32 bytes
struct apex_dvertex_lit_bump_t
{
    uint32_t positionIndex;
    uint32_t normalIndex;
    float albedoUV[2];
    int32_t negativeOne;        // Always -1
    float lightmapUV[2];
    uint8_t color[4];
};
static_assert(sizeof(apex_dvertex_lit_bump_t) == 32, "apex_dvertex_lit_bump_t must be 32 bytes");

// Apex v47 VertexLitFlat: 20 bytes
struct apex_dvertex_lit_flat_t
{
    uint32_t positionIndex;
    uint32_t normalIndex;
    float albedoUV[2];
    int32_t unknown;
};
static_assert(sizeof(apex_dvertex_lit_flat_t) == 20, "apex_dvertex_lit_flat_t must be 20 bytes");

// Apex v47 VertexUnlitTS: 24 bytes
struct apex_dvertex_unlit_ts_t
{
    uint32_t positionIndex;
    uint32_t normalIndex;
    float albedoUV[2];
    int32_t unknown[2];
};
static_assert(sizeof(apex_dvertex_unlit_ts_t) == 24, "apex_dvertex_unlit_ts_t must be 24 bytes");

// Apex v47 VertexBlinnPhong: 24 bytes
struct apex_dvertex_blinn_phong_t
{
    uint32_t positionIndex;
    uint32_t normalIndex;
    float albedoUV[2];
    float lightmapUV[2];
};
static_assert(sizeof(apex_dvertex_blinn_phong_t) == 24, "apex_dvertex_blinn_phong_t must be 24 bytes");

// Apex v47 Mesh: 28 bytes (format: "IH8hHI")
// DIFFERENT layout from TF2 despite same size!
struct apex_dmesh_t
{
    uint32_t firstMeshIndex;    // 4 bytes - I
    uint16_t numTriangles;      // 2 bytes - H
    int16_t unknown[8];         // 16 bytes - 8h (internal engine data, zeroed)
    uint16_t materialSort;      // 2 bytes - H
    uint32_t flags;             // 4 bytes - I
};
static_assert(sizeof(apex_dmesh_t) == 28, "apex_dmesh_t must be 28 bytes");

// Apex v47 VertexUnlit: 20 bytes (format: "2i2fi")
// TF2 has colour[4], Apex has unknown int
struct apex_dvertex_unlit_t
{
    uint32_t positionIndex;
    uint32_t normalIndex;
    float albedoUV[2];
    int32_t unknown;            // Apex uses -1, TF2 has RGBA colour
};
static_assert(sizeof(apex_dvertex_unlit_t) == 20, "apex_dvertex_unlit_t must be 20 bytes");

struct apex_entity_partition_header_t
{
    int16_t ident;  // '01' = 0x3130
};
#pragma pack(pop)

// ============================================================================
// Main Conversion Function
// ============================================================================

bool ConvertFromTF2(const std::string& bspPath, char* const bspBuf, const size_t fileSize, const bool packAllLumps)
{
    printf("Converting Titanfall 2 BSP (v37) to Apex Legends (v47)...\n");

    rmem buf(bspBuf);
    BSPHeader_t* pHdr = buf.get<BSPHeader_t>();

    if (pHdr->ident != 'PSBr')
    {
        printf("Error: Invalid BSP magic\n");
        return false;
    }

    if (pHdr->version != BSPVERSION_TF2)
    {
        printf("Error: Expected v37, got v%d\n", pHdr->version);
        return false;
    }

    // Store original lump info
    struct LumpInfo {
        int offset;
        int size;
        int version;
    };
    std::vector<LumpInfo> originalLumps(LUMP_COUNT);

    for (int i = 0; i < LUMP_COUNT; i++)
    {
        originalLumps[i].offset = pHdr->lumps[i].fileofs;
        originalLumps[i].size = pHdr->lumps[i].filelen;
        originalLumps[i].version = pHdr->lumps[i].version;
    }

    // Get TEXTURE_DATA_STRING_TABLE for converting TEXTURE_DATA name indices
    const char* stringTableData = nullptr;
    size_t stringTableSize = 0;
    if (originalLumps[tf2::LUMP_TEXDATA_STRING_TABLE].size > 0)
    {
        stringTableData = bspBuf + originalLumps[tf2::LUMP_TEXDATA_STRING_TABLE].offset;
        stringTableSize = originalLumps[tf2::LUMP_TEXDATA_STRING_TABLE].size;
    }

    // Prepare output
    std::string outputPath = bspPath;
    // Add _v47 suffix before .bsp
    size_t extPos = outputPath.rfind(".bsp");
    if (extPos != std::string::npos)
        outputPath.insert(extPos, "_v47");
    else
        outputPath += "_v47.bsp";

    CIOStream out;
    if (!out.Open(outputPath, CIOStream::WRITE | CIOStream::BINARY))
    {
        printf("Error: Failed to create output file: %s\n", outputPath.c_str());
        return false;
    }

    // Reserve space for header
    out.Seek(sizeof(BSPHeader_t));

    // Create new header
    BSPHeader_t newHdr = {};
    newHdr.ident = 'PSBr';
    newHdr.version = BSPVERSION;
    newHdr.mapRevision = pHdr->mapRevision;
    newHdr.lastLump = LUMP_COUNT - 1;
    newHdr.flags = 0;

    int currentOffset = sizeof(BSPHeader_t);


    // Storage for SURFACE_PROPERTIES generation
    std::vector<apex_dtexdata_t> storedTexdata;
    size_t surfaceNamesSize = 0;

    // Process each lump
    for (int i = 0; i < LUMP_COUNT; i++)
    {
        char* outData = nullptr;
        size_t outSize = 0;
        bool needsFree = false;

        const char* srcData = (originalLumps[i].size > 0) ? (bspBuf + originalLumps[i].offset) : nullptr;
        size_t srcSize = originalLumps[i].size;

        switch (i)
        {
        // ====================================================================
        // Lumps that need conversion
        // ====================================================================
        case LUMP_TEXDATA:
            if (srcSize > 0)
            {
                ConvertTextureData_TF2(srcData, srcSize, stringTableData, stringTableSize, outData, outSize);
                needsFree = true;
                printf("  Converted TEXTURE_DATA: %zu -> %zu bytes\n", srcSize, outSize);

                // Store for SURFACE_PROPERTIES generation
                size_t numTexdata = outSize / sizeof(apex_dtexdata_t);
                storedTexdata.resize(numTexdata);
                memcpy(storedTexdata.data(), outData, outSize);
            }
            break;

        case LUMP_MODELS:
            if (srcSize > 0)
            {
                ConvertModels_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted MODELS: %zu -> %zu bytes\n", srcSize, outSize);
            }
            break;

        case LUMP_MATERIAL_SORT:
            if (srcSize > 0)
            {
                ConvertMaterialSorts_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted MATERIAL_SORTS: %zu -> %zu bytes\n", srcSize, outSize);
            }
            break;

        case LUMP_LEVEL_INFO:
            if (srcSize > 0)
            {
                ConvertLevelInfo_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted LEVEL_INFO: %zu -> %zu bytes\n", srcSize, outSize);
            }
            break;

        case LUMP_VERTS_LIT_BUMP:
            if (srcSize > 0)
            {
                ConvertVertexLitBump_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted VERTEX_LIT_BUMP: %zu -> %zu bytes (%zu -> %zu verts)\n",
                       srcSize, outSize, srcSize/44, outSize/32);
            }
            break;

        case LUMP_VERTS_LIT_FLAT:
            if (srcSize > 0)
            {
                ConvertVertexLitFlat_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted VERTEX_LIT_FLAT: %zu -> %zu bytes (%zu -> %zu verts)\n",
                       srcSize, outSize, srcSize/36, outSize/20);
            }
            break;

        case LUMP_VERTS_UNLIT_TS:
            if (srcSize > 0)
            {
                ConvertVertexUnlitTS_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted VERTEX_UNLIT_TS: %zu -> %zu bytes\n", srcSize, outSize);
            }
            break;

        case LUMP_VERTS_BLINN_PHONG:
            if (srcSize > 0)
            {
                ConvertVertexBlinnPhong_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted VERTEX_BLINN_PHONG: %zu -> %zu bytes\n", srcSize, outSize);
            }
            break;

        case LUMP_MESHES:
            if (srcSize > 0)
            {
                ConvertMeshes_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted MESHES: %zu -> %zu bytes (%zu meshes)\n",
                       srcSize, outSize, srcSize / sizeof(tf2::dmesh_t));

            }
            break;

        case LUMP_VERTS_UNLIT:
            if (srcSize > 0)
            {
                ConvertVertexUnlit_TF2(srcData, srcSize, outData, outSize);
                needsFree = true;
                printf("  Converted VERTEX_UNLIT: %zu -> %zu bytes (%zu verts)\n",
                       srcSize, outSize, srcSize / sizeof(tf2::dvertex_unlit_t));
            }
            break;

        // ====================================================================
        // Lumps that need generation (new in Apex)
        // ====================================================================
        case LUMP_TEXDATA_STRING_DATA:  // This becomes SURFACE_NAMES in Apex (lump 0x0F)
        {
            // In Apex, lump 0x0F is SURFACE_NAMES, not TEXDATA_STRING_DATA
            // We need to copy TF2's TEXDATA_STRING_DATA (0x2B) to SURFACE_NAMES (0x0F)
            // This lump index (0x0F) needs special handling
            const char* tf2StringData = (originalLumps[tf2::LUMP_TEXDATA_STRING_DATA].size > 0)
                ? (bspBuf + originalLumps[tf2::LUMP_TEXDATA_STRING_DATA].offset) : nullptr;
            size_t tf2StringSize = originalLumps[tf2::LUMP_TEXDATA_STRING_DATA].size;

            if (tf2StringData && tf2StringSize > 0)
            {
                GenerateSurfaceNames_TF2(tf2StringData, tf2StringSize, outData, outSize);
                needsFree = true;
                surfaceNamesSize = outSize;  // Store for SURFACE_PROPERTIES generation
                printf("  Generated SURFACE_NAMES: %zu bytes\n", outSize);
            }
            break;
        }

        case LUMP_CONTENTS_MASKS:
            // Generate contents masks from texdata (or fallback to minimal)
            if (!storedTexdata.empty())
            {
                GenerateContentsMasks(storedTexdata, outData, outSize);
                needsFree = true;
                printf("  Generated CONTENTS_MASKS: %zu bytes (%zu masks)\n", outSize, outSize / sizeof(uint32_t));
            }
            else
            {
                GenerateEmptyContentsMasks(outData, outSize);
                needsFree = true;
                printf("  Generated CONTENTS_MASKS (placeholder): %zu bytes\n", outSize);
            }
            break;

        case LUMP_SURFACE_PROPERTIES:
            // Generate surface properties from texdata
            if (!storedTexdata.empty())
            {
                GenerateSurfaceProperties(storedTexdata, outData, outSize);
                needsFree = true;
                printf("  Generated SURFACE_PROPERTIES: %zu bytes (%zu entries)\n", outSize, outSize / 8);
            }
            else
            {
                GenerateEmptySurfaceProperties(outData, outSize);
                needsFree = true;
                printf("  Generated SURFACE_PROPERTIES (placeholder): %zu bytes\n", outSize);
            }
            break;

        case LUMP_BVH_NODES:
        case LUMP_BVH_LEAF_DATA:
            // Skip - will be generated together after loop
            break;

        case LUMP_PACKED_VERTICES:
            // Generate empty packed vertices for now
            GenerateEmptyPackedVertices(outData, outSize);
            needsFree = true;
            printf("  Generated PACKED_VERTICES (placeholder): %zu bytes\n", outSize);
            break;

        case LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS:
            // TF2 RTL format differs from Apex (TF2: 9 bytes/texel, Apex: ~6.5 bytes/texel)
            // Zero out for now - map will load without real-time lighting
            outData = nullptr;
            outSize = 0;
            printf("  Zeroed LIGHTMAP_DATA_REAL_TIME_LIGHTS (format incompatible)\n");
            break;

        case LUMP_ENTITY_PARTITIONS:
        {
            // TF2 entity partitions need to be converted/generated
            const char* entData = (originalLumps[LUMP_ENTITIES].size > 0)
                ? (bspBuf + originalLumps[LUMP_ENTITIES].offset) : nullptr;
            size_t entSize = originalLumps[LUMP_ENTITIES].size;

            if (srcSize > 0)
            {
                // Copy existing TF2 partition data but ensure Apex format
                outData = new char[srcSize];
                memcpy(outData, srcData, srcSize);
                outSize = srcSize;
                needsFree = true;
            }
            else if (entData)
            {
                GenerateEntityPartitions_TF2(entData, entSize, outData, outSize);
                needsFree = true;
            }
            printf("  Processed ENTITY_PARTITIONS: %zu bytes\n", outSize);
            break;
        }

        // ====================================================================
        // Lumps to zero out (TF2-specific, not used in Apex)
        // ====================================================================
        case tf2::LUMP_LIGHTPROBE_BSP_NODES:
        case tf2::LUMP_LIGHTPROBE_BSP_REF_IDS:
        case tf2::LUMP_PHYSICS_COLLIDE:
        case tf2::LUMP_LEAF_WATER_DATA:
        case tf2::LUMP_PHYSICS_LEVEL:
        case tf2::LUMP_TRICOLL_TRIS:
        case tf2::LUMP_TRICOLL_NODES:
        case tf2::LUMP_TRICOLL_HEADERS:
        case tf2::LUMP_PHYSICS_TRIANGLES:
        case tf2::LUMP_CM_GRID:
        case tf2::LUMP_CM_GRID_CELLS:
        case tf2::LUMP_CM_GEO_SETS:
        case tf2::LUMP_CM_GEO_SET_BOUNDS:
        case tf2::LUMP_CM_PRIMITIVES:
        case tf2::LUMP_CM_PRIMITIVE_BOUNDS:
        case tf2::LUMP_CM_UNIQUE_CONTENTS:
        case tf2::LUMP_CM_BRUSHES:
        case tf2::LUMP_CM_BRUSH_SIDE_PLANE_OFFSETS:
        case tf2::LUMP_CM_BRUSH_SIDE_PROPS:
        case tf2::LUMP_CM_BRUSH_SIDE_TEX_VECS:
        case tf2::LUMP_TRICOLL_BEVEL_STARTS:
        case tf2::LUMP_TRICOLL_BEVEL_INDICES:
        case tf2::LUMP_TEXDATA_STRING_TABLE:    // Absorbed into SURFACE_NAMES
            // Zero out these lumps
            outData = nullptr;
            outSize = 0;
            break;

        // ====================================================================
        // Direct-copy lumps
        // ====================================================================
        case LUMP_VERTEXES:
            // Direct copy
            if (srcSize > 0)
            {
                outData = const_cast<char*>(srcData);
                outSize = srcSize;
                needsFree = false;

            }
            break;

        case LUMP_MESH_INDICES:
            // Direct copy
            if (srcSize > 0)
            {
                outData = const_cast<char*>(srcData);
                outSize = srcSize;
                needsFree = false;

            }
            break;

        // ====================================================================
        // Direct copy lumps (format unchanged between TF2 and Apex v47)
        // ====================================================================
        case LUMP_ENTITIES:
        case LUMP_PLANES:
        case LUMP_LIGHTPROBE_PARENT_INFOS:
        case LUMP_SHADOW_ENVIRONMENTS:
        case LUMP_VERTNORMALS:
        case LUMP_GAME_LUMP:
        case LUMP_PAKFILE:
        case LUMP_CUBEMAPS:
        case LUMP_UNKNOWN_43:  // Keep if present
        case LUMP_WORLD_LIGHTS:
        case LUMP_WORLD_LIGHT_PARENT_INFOS:
        // LUMP_VERTS_UNLIT - converted above (TF2 has colour, Apex has unknown int)
        // LUMP_MESH_INDICES - handled above
        // LUMP_MESHES - converted above (different field layouts despite same size)
        case LUMP_MESH_BOUNDS:
        case LUMP_LIGHTMAP_HEADERS:
        case LUMP_LIGHTMAP_DATA_SKY:
        case LUMP_CSM_AABB_NODES:
        case LUMP_CSM_OBJ_REFS:
        case LUMP_LIGHTPROBES:  // 48 bytes, same format
        case LUMP_STATIC_PROP_LIGHTPROBE_INDEX:
        case LUMP_LIGHTPROBETREE:
        case LUMP_LIGHTPROBEREFS:
        // LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS - NOT direct copy, format differs (TF2: 9 bytes/texel, Apex: ~6.5 bytes/texel)
        case LUMP_CELL_BSP_NODES:
        case LUMP_CELLS:
        case LUMP_PORTALS:
        case LUMP_PORTAL_VERTS:
        case LUMP_PORTAL_EDGES:
        case LUMP_PORTAL_VERT_EDGES:
        case LUMP_PORTAL_VERT_REFS:
        case LUMP_PORTAL_EDGE_REFS:
        case LUMP_PORTAL_EDGE_ISECT_EDGE:
        case LUMP_PORTAL_EDGE_ISECT_AT_VERT:
        case LUMP_PORTAL_EDGE_ISECT_HEADER:
        case LUMP_OCCLUSIONMESH_VERTS:
        case LUMP_OCCLUSIONMESH_INDICES:
        case LUMP_CELL_AABB_NODES:
        case LUMP_OBJ_REFS:
        case LUMP_OBJ_REF_BOUNDS:
        case LUMP_LIGHTMAP_DATA_RTL_PAGE:
        case LUMP_SHADOW_MESH_OPAQUE_VERTS:
        case LUMP_SHADOW_MESH_ALPHA_VERTS:
        case LUMP_SHADOW_MESH_INDICES:
        case LUMP_SHADOW_MESH_MESHES:
        default:
            if (srcSize > 0)
            {
                outData = const_cast<char*>(srcData);
                outSize = srcSize;
                needsFree = false;
            }
            break;
        }

        // Write lump to output
        if (outSize > 0 && outData != nullptr)
        {
            if (packAllLumps)
            {
                // Pack into single BSP file
                newHdr.lumps[i].fileofs = currentOffset;
                newHdr.lumps[i].filelen = (int)outSize;
                newHdr.lumps[i].version = 0;
                newHdr.lumps[i].uncompLen = 0;

                out.Write(outData, outSize);
                currentOffset += (int)outSize;

                // Align to 4 bytes
                int padding = (4 - (currentOffset % 4)) % 4;
                if (padding > 0)
                {
                    char padBytes[4] = {0};
                    out.Write(padBytes, padding);
                    currentOffset += padding;
                }
            }
            else
            {
                // Write to external .bsp_lump file
                WriteLumpExternal(outputPath, i, outData, outSize);

                // Header points to external file (fileofs = 0, filelen = size)
                newHdr.lumps[i].fileofs = 0;
                newHdr.lumps[i].filelen = (int)outSize;
                newHdr.lumps[i].version = 0;
                newHdr.lumps[i].uncompLen = 0;
            }
        }
        else
        {
            newHdr.lumps[i].fileofs = 0;
            newHdr.lumps[i].filelen = 0;
            newHdr.lumps[i].version = 0;
            newHdr.lumps[i].uncompLen = 0;
        }

        if (needsFree && outData)
            delete[] outData;
    }


    // Fix GAME_LUMP offset
    if (newHdr.lumps[LUMP_GAME_LUMP].filelen > 0)
    {
        // Calculate the data offset within the game lump
        int dataOffset = sizeof(int) + sizeof(tf2::dgamelump_t);

        if (packAllLumps)
        {
            // Re-read and fix the game lump offset in the packed BSP
            out.Seek(newHdr.lumps[LUMP_GAME_LUMP].fileofs);
            int numGameLumps;
            out.Read(&numGameLumps, sizeof(int));

            if (numGameLumps == 1)
            {
                tf2::dgamelump_t gameLump;
                out.Seek(newHdr.lumps[LUMP_GAME_LUMP].fileofs + sizeof(int));
                out.Read(&gameLump, sizeof(gameLump));

                gameLump.fileofs = newHdr.lumps[LUMP_GAME_LUMP].fileofs + dataOffset;

                out.Seek(newHdr.lumps[LUMP_GAME_LUMP].fileofs + sizeof(int));
                out.Write(&gameLump, sizeof(gameLump));
            }
        }
        else
        {
            // Fix the offset in the external lump file
            std::string lumpPath = outputPath + "." + Format("%04x", LUMP_GAME_LUMP) + ".bsp_lump";
            CIOStream lumpFile;
            if (lumpFile.Open(lumpPath, CIOStream::READ | CIOStream::WRITE | CIOStream::BINARY))
            {
                int numGameLumps;
                lumpFile.Read(&numGameLumps, sizeof(int));

                if (numGameLumps == 1)
                {
                    tf2::dgamelump_t gameLump;
                    lumpFile.Seek(sizeof(int));
                    lumpFile.Read(&gameLump, sizeof(gameLump));

                    // For external lumps, offset is relative to start of lump file
                    gameLump.fileofs = dataOffset;

                    lumpFile.Seek(sizeof(int));
                    lumpFile.Write(&gameLump, sizeof(gameLump));
                }
            }
        }
    }

    // Write header
    out.Seek(0);
    out.Write(&newHdr, sizeof(BSPHeader_t));

    if (packAllLumps)
    {
        printf("Conversion complete! Output: %s\n", outputPath.c_str());
        printf("  Total size: %d bytes\n", currentOffset);
    }
    else
    {
        printf("Conversion complete! Output: %s (+ external lumps)\n", outputPath.c_str());
    }

    return true;
}

// ============================================================================
// Conversion Functions Implementation
// ============================================================================

static void ConvertTextureData_TF2(const char* srcData, size_t srcSize,
                                    const char* stringTableData, size_t stringTableSize,
                                    char*& dstData, size_t& dstSize)
{
    const size_t numEntries = srcSize / sizeof(tf2::dtexdata_t);
    dstSize = numEntries * sizeof(apex_dtexdata_t);
    dstData = new char[dstSize];

    const tf2::dtexdata_t* src = reinterpret_cast<const tf2::dtexdata_t*>(srcData);
    apex_dtexdata_t* dst = reinterpret_cast<apex_dtexdata_t*>(dstData);

    // Build string table lookup if available
    std::vector<int32_t> stringOffsets;
    if (stringTableData && stringTableSize > 0)
    {
        const int32_t* table = reinterpret_cast<const int32_t*>(stringTableData);
        size_t numStrings = stringTableSize / sizeof(int32_t);
        for (size_t i = 0; i < numStrings; i++)
            stringOffsets.push_back(table[i]);
    }

    for (size_t i = 0; i < numEntries; i++)
    {
        // Convert name index: TF2 uses STRING_TABLE index, Apex uses direct byte offset
        int32_t nameOffset = 0;
        if (src[i].nameStringTableID >= 0 && (size_t)src[i].nameStringTableID < stringOffsets.size())
            nameOffset = stringOffsets[src[i].nameStringTableID];

        dst[i].nameIndex = nameOffset;
        dst[i].width = src[i].width;
        dst[i].height = src[i].height;
        dst[i].flags = src[i].flags;
    }
}

static void ConvertModels_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    const size_t numModels = srcSize / sizeof(tf2::dmodel_t);
    dstSize = numModels * sizeof(apex_dmodel_t);
    dstData = new char[dstSize];
    memset(dstData, 0, dstSize);

    const tf2::dmodel_t* src = reinterpret_cast<const tf2::dmodel_t*>(srcData);
    apex_dmodel_t* dst = reinterpret_cast<apex_dmodel_t*>(dstData);

    for (size_t i = 0; i < numModels; i++)
    {
        dst[i].mins = src[i].mins;
        dst[i].maxs = src[i].maxs;
        dst[i].firstMesh = src[i].firstMesh;
        dst[i].meshCount = src[i].meshCount;
        dst[i].bvhNode = -1;        // No BVH collision
        dst[i].bvhLeaf = -1;        // No BVH collision
        dst[i].firstVertex = 0;
        dst[i].vertexFlags = 0;
        dst[i].packedVertexOffset = Vector(0, 0, 0);
        dst[i].nodeScale = 1.0f;
    }
}

static void ConvertMaterialSorts_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    // MaterialSort is 12 bytes in both TF2 and Apex - same layout
    // TF2:  texdata(2) + lightmapHeader(2) + cubemap(2) + lastVertex(2) + vertexOffset(4)
    // Apex: texdata(2) + lightmapHeader(2) + unknown[2](4) + vertexOffset(4)
    // We can direct copy since memory layout is identical
    dstSize = srcSize;
    dstData = new char[dstSize];
    memcpy(dstData, srcData, srcSize);
}

static void ConvertLevelInfo_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    dstSize = sizeof(apex_dlevelinfo_t);
    dstData = new char[dstSize];
    memset(dstData, 0, dstSize);

    if (srcSize >= sizeof(tf2::dlevelinfo_t))
    {
        const tf2::dlevelinfo_t* src = reinterpret_cast<const tf2::dlevelinfo_t*>(srcData);
        apex_dlevelinfo_t* dst = reinterpret_cast<apex_dlevelinfo_t*>(dstData);

        dst->unknown[0] = src->firstDecalMesh;
        dst->unknown[1] = src->firstTransparentMesh;
        dst->unknown[2] = src->firstSkyMesh;
        dst->unknown[3] = 0;
        dst->numStaticProps = src->numStaticProps;
        dst->sunNormal = src->sunNormal;
        dst->numEntityModels = 0;  // Will need to be calculated from entities
    }
}

static void ConvertVertexLitBump_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    const size_t numVerts = srcSize / sizeof(tf2::dvertex_lit_bump_t);
    dstSize = numVerts * sizeof(apex_dvertex_lit_bump_t);
    dstData = new char[dstSize];

    const tf2::dvertex_lit_bump_t* src = reinterpret_cast<const tf2::dvertex_lit_bump_t*>(srcData);
    apex_dvertex_lit_bump_t* dst = reinterpret_cast<apex_dvertex_lit_bump_t*>(dstData);

    for (size_t i = 0; i < numVerts; i++)
    {
        dst[i].positionIndex = src[i].positionIndex;
        dst[i].normalIndex = src[i].normalIndex;
        dst[i].albedoUV[0] = src[i].albedoUV[0];
        dst[i].albedoUV[1] = src[i].albedoUV[1];
        dst[i].negativeOne = -1;
        dst[i].lightmapUV[0] = src[i].lightmapUV[0];
        dst[i].lightmapUV[1] = src[i].lightmapUV[1];
        dst[i].color[0] = src[i].color[0];
        dst[i].color[1] = src[i].color[1];
        dst[i].color[2] = src[i].color[2];
        dst[i].color[3] = src[i].color[3];
    }
}

static void ConvertVertexLitFlat_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    // TF2: 36 bytes (2I2f4B4f) -> Apex: 20 bytes (2I2fI)
    const size_t numVerts = srcSize / sizeof(tf2::dvertex_lit_flat_t);
    dstSize = numVerts * sizeof(apex_dvertex_lit_flat_t);
    dstData = new char[dstSize];

    const tf2::dvertex_lit_flat_t* src = reinterpret_cast<const tf2::dvertex_lit_flat_t*>(srcData);
    apex_dvertex_lit_flat_t* dst = reinterpret_cast<apex_dvertex_lit_flat_t*>(dstData);

    for (size_t i = 0; i < numVerts; i++)
    {
        dst[i].positionIndex = src[i].positionIndex;
        dst[i].normalIndex = src[i].normalIndex;
        dst[i].albedoUV[0] = src[i].albedoUV[0];
        dst[i].albedoUV[1] = src[i].albedoUV[1];
        // Apex removes color, lightmapUV, lightmapStep - just sets unknown field
        dst[i].unknown = -1;
    }
}

static void ConvertVertexUnlitTS_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    const size_t numVerts = srcSize / sizeof(tf2::dvertex_unlit_ts_t);
    dstSize = numVerts * sizeof(apex_dvertex_unlit_ts_t);
    dstData = new char[dstSize];

    const tf2::dvertex_unlit_ts_t* src = reinterpret_cast<const tf2::dvertex_unlit_ts_t*>(srcData);
    apex_dvertex_unlit_ts_t* dst = reinterpret_cast<apex_dvertex_unlit_ts_t*>(dstData);

    for (size_t i = 0; i < numVerts; i++)
    {
        dst[i].positionIndex = src[i].positionIndex;
        dst[i].normalIndex = src[i].normalIndex;
        dst[i].albedoUV[0] = src[i].albedoUV[0];
        dst[i].albedoUV[1] = src[i].albedoUV[1];
        dst[i].unknown[0] = src[i].tangent[0];
        dst[i].unknown[1] = src[i].tangent[1];
    }
}

static void ConvertVertexBlinnPhong_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    const size_t numVerts = srcSize / sizeof(tf2::dvertex_blinn_phong_t);
    dstSize = numVerts * sizeof(apex_dvertex_blinn_phong_t);
    dstData = new char[dstSize];

    const tf2::dvertex_blinn_phong_t* src = reinterpret_cast<const tf2::dvertex_blinn_phong_t*>(srcData);
    apex_dvertex_blinn_phong_t* dst = reinterpret_cast<apex_dvertex_blinn_phong_t*>(dstData);

    for (size_t i = 0; i < numVerts; i++)
    {
        dst[i].positionIndex = src[i].positionIndex;
        dst[i].normalIndex = src[i].normalIndex;
        dst[i].albedoUV[0] = src[i].albedoUV[0];
        dst[i].albedoUV[1] = src[i].albedoUV[1];
        dst[i].lightmapUV[0] = src[i].lightmapUV[0];
        dst[i].lightmapUV[1] = src[i].lightmapUV[1];
    }
}

static void ConvertMeshes_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    // TF2 Mesh format: "I3H6b2h2BHI" = 28 bytes
    // Apex Mesh format: "IH8hHI" = 28 bytes (same size, different layout!)
    //
    // TF2 fields:
    //   firstMeshIndex(4), numTriangles(2), firstVertex(2), numVertices(2),
    //   vertexType(1), cubemap(1), styles[4](4), luxelOrigin[2](4), luxelOffsetMax[2](2),
    //   materialSort(2), flags(4)
    //
    // Apex fields:
    //   firstMeshIndex(4), numTriangles(2), unknown[8](16), materialSort(2), flags(4)
    //
    // IMPORTANT: Apex does NOT use firstVertex/numVertices in the Mesh struct!
    // Instead, it uses MaterialSort's vertex_offset. The unknown[8] array is
    // internal engine data that should be ZEROED, not filled with TF2 values.

    const size_t numMeshes = srcSize / sizeof(tf2::dmesh_t);
    dstSize = numMeshes * sizeof(apex_dmesh_t);
    dstData = new char[dstSize];
    memset(dstData, 0, dstSize);

    const tf2::dmesh_t* src = reinterpret_cast<const tf2::dmesh_t*>(srcData);
    apex_dmesh_t* dst = reinterpret_cast<apex_dmesh_t*>(dstData);

    for (size_t i = 0; i < numMeshes; i++)
    {
        dst[i].firstMeshIndex = src[i].firstMeshIndex;
        dst[i].numTriangles = src[i].numTriangles;

        // CRITICAL: Zero the unknown[8] array - Apex uses this for internal engine data
        // Filling it with TF2 data causes crashes as the engine misinterprets the values
        for (int j = 0; j < 8; j++)
            dst[i].unknown[j] = 0;

        dst[i].materialSort = src[i].materialSort;
        dst[i].flags = src[i].flags;
    }
}

static void ConvertVertexUnlit_TF2(const char* srcData, size_t srcSize, char*& dstData, size_t& dstSize)
{
    // TF2 VertexUnlit format: "2I2f4B" = 20 bytes
    // Apex VertexUnlit format: "2i2fi" = 20 bytes (same size, different last field!)
    //
    // TF2 fields: positionIndex(4), normalIndex(4), albedoUV[2](8), colour[4](4)
    // Apex fields: positionIndex(4), normalIndex(4), albedoUV[2](8), unknown(4)
    //
    // TF2 ends with RGBA colour, Apex ends with an int (typically -1)

    const size_t numVerts = srcSize / sizeof(tf2::dvertex_unlit_t);
    dstSize = numVerts * sizeof(apex_dvertex_unlit_t);
    dstData = new char[dstSize];

    const tf2::dvertex_unlit_t* src = reinterpret_cast<const tf2::dvertex_unlit_t*>(srcData);
    apex_dvertex_unlit_t* dst = reinterpret_cast<apex_dvertex_unlit_t*>(dstData);

    for (size_t i = 0; i < numVerts; i++)
    {
        dst[i].positionIndex = src[i].positionIndex;
        dst[i].normalIndex = src[i].normalIndex;
        dst[i].albedoUV[0] = src[i].albedoUV[0];
        dst[i].albedoUV[1] = src[i].albedoUV[1];
        // TF2 has RGBA colour, Apex expects an int (typically -1)
        // We set to -1 as that's what Apex maps use
        dst[i].unknown = -1;
    }
}

static void GenerateEntityPartitions_TF2(const char* entitiesData, size_t entitiesSize, char*& dstData, size_t& dstSize)
{
    // Generate minimal entity partition header
    // Format: "01" (ident) + "*" (hasEnts) + partition names
    const char* defaultPartition = "01*env fx script snd spawn";
    dstSize = strlen(defaultPartition) + 1;
    dstData = new char[dstSize];
    memcpy(dstData, defaultPartition, dstSize);
}

static void GenerateSurfaceNames_TF2(const char* stringData, size_t stringDataSize, char*& dstData, size_t& dstSize)
{
    // Direct copy - SURFACE_NAMES has same format as TEXDATA_STRING_DATA
    dstSize = stringDataSize;
    dstData = new char[dstSize];
    memcpy(dstData, stringData, stringDataSize);
}

// Apex surface property entry (8 bytes)
#pragma pack(push, 1)
struct apex_surfaceprop_t
{
    uint16_t flags;         // Surface flags (from texdata)
    uint8_t surfaceId;      // Surface/material type ID
    uint8_t contentsIdx;    // Index into CONTENTS_MASKS
    int32_t nameOffset;     // Offset into SURFACE_NAMES
};
#pragma pack(pop)
static_assert(sizeof(apex_surfaceprop_t) == 8, "apex_surfaceprop_t must be 8 bytes");

static void GenerateContentsMasks(const std::vector<apex_dtexdata_t>& texdata, char*& dstData, size_t& dstSize)
{
    // Generate contents masks from texdata flags
    // Each unique contents mask gets an entry
    std::vector<uint32_t> contentsMasks;

    // Always have at least CONTENTS_SOLID
    contentsMasks.push_back(0x00000001);  // CONTENTS_SOLID

    // Common contents masks used in Apex
    contentsMasks.push_back(0x00A30000);  // PLAYERCLIP/MONSTERCLIP
    contentsMasks.push_back(0x00230000);  // SKY
    contentsMasks.push_back(0x00400000);  // TRIGGER
    contentsMasks.push_back(0x00110000);  // NODRAW
    contentsMasks.push_back(0x00B10000);  // BLOCKLOS
    contentsMasks.push_back(0x00E31240);  // DETAIL
    contentsMasks.push_back(0x00020000);  // WATER

    dstSize = contentsMasks.size() * sizeof(uint32_t);
    dstData = new char[dstSize];
    memcpy(dstData, contentsMasks.data(), dstSize);
}

static void GenerateSurfaceProperties(const std::vector<apex_dtexdata_t>& texdata, char*& dstData, size_t& dstSize)
{
    // Generate surface properties from texdata
    // Each texdata entry gets a corresponding surface property

    if (texdata.empty())
    {
        dstSize = 0;
        dstData = nullptr;
        return;
    }

    dstSize = texdata.size() * sizeof(apex_surfaceprop_t);
    dstData = new char[dstSize];

    apex_surfaceprop_t* dst = reinterpret_cast<apex_surfaceprop_t*>(dstData);

    for (size_t i = 0; i < texdata.size(); i++)
    {
        const apex_dtexdata_t& td = texdata[i];

        // Map texdata flags to surface property flags
        // Common flag mappings observed in working maps:
        // 0x0200 = standard solid surface
        // 0x0480 = clip/invisible
        // 0x0680 = nodraw
        // 0x0A00 = standard visible
        uint16_t surfFlags = 0x0200;  // Default to solid
        if (td.flags & 0x0080)
            surfFlags = 0x0680;  // NODRAW
        else if (td.flags & 0x0400)
            surfFlags = 0x0480;  // CLIP

        // Determine surface type from material name hints
        // 0x00 = default
        // 0x07 = concrete
        // 0x09 = metal
        // 0x15/0x16 = dirt/grass
        // 0x30 = wood
        // 0x57 = other
        uint8_t surfaceId = 0x00;  // Default surface

        // Contents index - map based on flags
        // 0 = solid, 1 = water, 2 = playerclip, etc.
        uint8_t contentsIdx = 0;  // CONTENTS_SOLID
        if (td.flags & 0x0400)
            contentsIdx = 1;  // Clip surfaces

        dst[i].flags = surfFlags;
        dst[i].surfaceId = surfaceId;
        dst[i].contentsIdx = contentsIdx;
        dst[i].nameOffset = td.nameIndex;  // Use the name offset from texdata
    }
}

// Legacy placeholder (kept for compatibility)
static void GenerateEmptyContentsMasks(char*& dstData, size_t& dstSize)
{
    // Generate minimal contents mask (just solid)
    dstSize = sizeof(uint32_t);
    dstData = new char[dstSize];
    *reinterpret_cast<uint32_t*>(dstData) = 0x1;  // CONTENTS_SOLID
}

static void GenerateEmptySurfaceProperties(char*& dstData, size_t& dstSize)
{
    // Generate empty surface properties
    dstSize = 0;
    dstData = nullptr;
}

static void GenerateEmptyPackedVertices(char*& dstData, size_t& dstSize)
{
    // Generate empty packed vertices
    dstSize = 0;
    dstData = nullptr;
}

static void WriteLumpExternal(const std::string& basePath, int lumpIndex, const char* data, size_t size)
{
    // Write lump to external file: basepath.XXXX.bsp_lump
    char lumpFileName[16];
    snprintf(lumpFileName, sizeof(lumpFileName), ".%04x.bsp_lump", lumpIndex);

    std::string lumpPath = basePath + lumpFileName;

    CIOStream outLump;
    if (outLump.Open(lumpPath, CIOStream::WRITE | CIOStream::BINARY))
    {
        outLump.Write(data, size);
        printf("  Wrote external lump %s (%zu bytes)\n", lumpFileName, size);
    }
    else
    {
        printf("  ERROR: Failed to write external lump: %s\n", lumpPath.c_str());
    }
}
