#pragma once
#include <stdint.h>

// Titanfall 2 BSP v37 structure definitions
// Used for converting TF2 maps to Apex Legends v47 format

#define BSPVERSION_TF2 37

// Simple Vector type for BSP structures
struct Vector
{
    float x, y, z;

    Vector() : x(0), y(0), z(0) {}
    Vector(float _x, float _y, float _z) : x(_x), y(_y), z(_z) {}
};
static_assert(sizeof(Vector) == 12, "Vector must be 12 bytes");

namespace tf2
{
    // ============================================================================
    // Lump indices for TF2 v37 (different from Apex in several places)
    // ============================================================================
    enum lumptype_t
    {
        LUMP_ENTITIES = 0x0000,
        LUMP_PLANES = 0x0001,
        LUMP_TEXDATA = 0x0002,
        LUMP_VERTEXES = 0x0003,
        LUMP_LIGHTPROBE_PARENT_INFOS = 0x0004,
        LUMP_SHADOW_ENVIRONMENTS = 0x0005,
        LUMP_LIGHTPROBE_BSP_NODES = 0x0006,      // Removed in Apex
        LUMP_LIGHTPROBE_BSP_REF_IDS = 0x0007,    // Removed in Apex
        LUMP_MODELS = 0x000E,
        LUMP_ENTITY_PARTITIONS = 0x0018,
        LUMP_PHYSICS_COLLIDE = 0x001D,           // Removed in Apex
        LUMP_VERTNORMALS = 0x001E,
        LUMP_GAME_LUMP = 0x0023,
        LUMP_LEAF_WATER_DATA = 0x0024,           // Removed in Apex
        LUMP_PAKFILE = 0x0028,
        LUMP_CUBEMAPS = 0x002A,
        LUMP_TEXDATA_STRING_DATA = 0x002B,       // -> SURFACE_NAMES in Apex
        LUMP_TEXDATA_STRING_TABLE = 0x002C,      // Removed in Apex (absorbed)
        LUMP_WORLD_LIGHTS = 0x0036,
        LUMP_WORLD_LIGHT_PARENT_INFOS = 0x0037,
        LUMP_PHYSICS_LEVEL = 0x003E,             // Removed in Apex
        LUMP_TRICOLL_TRIS = 0x0042,              // Removed in Apex
        LUMP_TRICOLL_NODES = 0x0044,             // Removed in Apex
        LUMP_TRICOLL_HEADERS = 0x0045,           // Removed in Apex
        LUMP_PHYSICS_TRIANGLES = 0x0046,         // Removed in Apex
        LUMP_VERTS_UNLIT = 0x0047,
        LUMP_VERTS_LIT_FLAT = 0x0048,
        LUMP_VERTS_LIT_BUMP = 0x0049,
        LUMP_VERTS_UNLIT_TS = 0x004A,
        LUMP_VERTS_BLINN_PHONG = 0x004B,
        LUMP_MESH_INDICES = 0x004F,
        LUMP_MESHES = 0x0050,
        LUMP_MESH_BOUNDS = 0x0051,
        LUMP_MATERIAL_SORT = 0x0052,
        LUMP_LIGHTMAP_HEADERS = 0x0053,
        LUMP_CM_GRID = 0x0055,                   // Removed in Apex
        LUMP_CM_GRID_CELLS = 0x0056,             // Removed in Apex
        LUMP_CM_GEO_SETS = 0x0057,               // Removed in Apex
        LUMP_CM_GEO_SET_BOUNDS = 0x0058,         // Removed in Apex
        LUMP_CM_PRIMITIVES = 0x0059,             // Removed in Apex
        LUMP_CM_PRIMITIVE_BOUNDS = 0x005A,       // Removed in Apex
        LUMP_CM_UNIQUE_CONTENTS = 0x005B,        // Removed in Apex
        LUMP_CM_BRUSHES = 0x005C,                // Removed in Apex
        LUMP_CM_BRUSH_SIDE_PLANE_OFFSETS = 0x005D,
        LUMP_CM_BRUSH_SIDE_PROPS = 0x005E,       // Removed in Apex
        LUMP_CM_BRUSH_SIDE_TEX_VECS = 0x005F,    // Removed in Apex
        LUMP_TRICOLL_BEVEL_STARTS = 0x0060,      // Removed in Apex
        LUMP_TRICOLL_BEVEL_INDICES = 0x0061,     // Removed in Apex
        LUMP_LIGHTMAP_DATA_SKY = 0x0062,
        LUMP_CSM_AABB_NODES = 0x0063,
        LUMP_CSM_OBJ_REFS = 0x0064,
        LUMP_LIGHTPROBES = 0x0065,
        LUMP_STATIC_PROP_LIGHTPROBE_INDEX = 0x0066,
        LUMP_LIGHTPROBETREE = 0x0067,
        LUMP_LIGHTPROBEREFS = 0x0068,
        LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS = 0x0069,
        LUMP_CELL_BSP_NODES = 0x006A,
        LUMP_CELLS = 0x006B,
        LUMP_PORTALS = 0x006C,
        LUMP_PORTAL_VERTS = 0x006D,
        LUMP_PORTAL_EDGES = 0x006E,
        LUMP_PORTAL_VERT_EDGES = 0x006F,
        LUMP_PORTAL_VERT_REFS = 0x0070,
        LUMP_PORTAL_EDGE_REFS = 0x0071,
        LUMP_PORTAL_EDGE_ISECT_EDGE = 0x0072,
        LUMP_PORTAL_EDGE_ISECT_AT_VERT = 0x0073,
        LUMP_PORTAL_EDGE_ISECT_HEADER = 0x0074,
        LUMP_OCCLUSIONMESH_VERTS = 0x0075,
        LUMP_OCCLUSIONMESH_INDICES = 0x0076,
        LUMP_CELL_AABB_NODES = 0x0077,
        LUMP_OBJ_REFS = 0x0078,
        LUMP_OBJ_REF_BOUNDS = 0x0079,
        LUMP_LIGHTMAP_DATA_RTL_PAGE = 0x007A,
        LUMP_LEVEL_INFO = 0x007B,
        LUMP_SHADOW_MESH_OPAQUE_VERTS = 0x007C,
        LUMP_SHADOW_MESH_ALPHA_VERTS = 0x007D,
        LUMP_SHADOW_MESH_INDICES = 0x007E,
        LUMP_SHADOW_MESH_MESHES = 0x007F,
    };

    // ============================================================================
    // TF2 Structure Definitions (sizes differ from Apex v47)
    // ============================================================================

    // TextureData: 36 bytes in TF2, 16 bytes in Apex v47
    struct dtexdata_t
    {
        float reflectivity[3];      // 12 bytes - REMOVED in Apex
        int32_t nameStringTableID;  // 4 bytes - Index into STRING_TABLE (needs conversion)
        int32_t width;              // 4 bytes
        int32_t height;             // 4 bytes
        int32_t view_width;         // 4 bytes - REMOVED in Apex
        int32_t view_height;        // 4 bytes - REMOVED in Apex
        int32_t flags;              // 4 bytes
    };
    static_assert(sizeof(dtexdata_t) == 36, "TF2 dtexdata_t must be 36 bytes");

    // Model: 32 bytes in TF2, 64 bytes in Apex v47
    struct dmodel_t
    {
        Vector mins;                // 12 bytes
        Vector maxs;                // 12 bytes
        int32_t firstMesh;          // 4 bytes
        int32_t meshCount;          // 4 bytes
    };
    static_assert(sizeof(dmodel_t) == 32, "TF2 dmodel_t must be 32 bytes");

    // Mesh: 28 bytes in TF2 (format: "I3H6b2h2BHI")
    // DIFFERENT from Apex v47 format! (Apex: "IH8hHI")
    #pragma pack(push, 1)
    struct dmesh_t
    {
        uint32_t firstMeshIndex;    // 4 bytes - I
        uint16_t numTriangles;      // 2 bytes - H
        uint16_t firstVertex;       // 2 bytes - H (index into VertexReservedX)
        uint16_t numVertices;       // 2 bytes - H
        int8_t vertexType;          // 1 byte  - b
        int8_t cubemap;             // 1 byte  - b (index into Cubemaps, -1 if None)
        int8_t styles[4];           // 4 bytes - 4b (lighting states)
        int16_t luxelOrigin[2];     // 4 bytes - 2h (lightmap mins)
        uint8_t luxelOffsetMax[2];  // 2 bytes - 2B (lightmap size)
        uint16_t materialSort;      // 2 bytes - H (index into MaterialSorts)
        uint32_t flags;             // 4 bytes - I (MeshFlags)
    };
    #pragma pack(pop)
    static_assert(sizeof(dmesh_t) == 28, "TF2 dmesh_t must be 28 bytes");

    // MaterialSort: 12 bytes in both TF2 and Apex v47 (SAME structure!)
    struct dmaterialsort_t
    {
        int16_t texdata;            // 2 bytes
        int16_t lightmapHeader;     // 2 bytes
        int16_t cubemap;            // 2 bytes
        int16_t lastVertex;         // 2 bytes
        int32_t vertexOffset;       // 4 bytes
    };
    static_assert(sizeof(dmaterialsort_t) == 12, "TF2 dmaterialsort_t must be 12 bytes");

    // LevelInfo: 28 bytes in TF2, 36 bytes in Apex v47
    struct dlevelinfo_t
    {
        int32_t firstDecalMesh;         // 4 bytes - Becomes unknown[0]
        int32_t firstTransparentMesh;   // 4 bytes - Becomes unknown[1]
        int32_t firstSkyMesh;           // 4 bytes - Becomes unknown[2]
        int32_t numStaticProps;         // 4 bytes
        Vector sunNormal;               // 12 bytes
    };
    static_assert(sizeof(dlevelinfo_t) == 28, "TF2 dlevelinfo_t must be 28 bytes");

    // WorldLight v1: 100 bytes (TF2 can use v1, v2, or v3)
    struct dworldlight_v1_t
    {
        Vector origin;              // 12 bytes
        Vector intensity;           // 12 bytes
        Vector normal;              // 12 bytes
        Vector shadow_offset;       // 12 bytes (new in Titanfall)
        int32_t viscluster;         // 4 bytes
        int32_t type;               // 4 bytes (EmitType)
        int32_t style;              // 4 bytes
        float stopdot;              // 4 bytes
        float stopdot2;             // 4 bytes
        float exponent;             // 4 bytes
        float radius;               // 4 bytes
        float constant_attn;        // 4 bytes
        float linear_attn;          // 4 bytes
        float quadratic_attn;       // 4 bytes
        int32_t flags;              // 4 bytes
        int32_t texdata;            // 4 bytes
        int32_t parent;             // 4 bytes
    };
    static_assert(sizeof(dworldlight_v1_t) == 100, "TF2 dworldlight_v1_t must be 100 bytes");

    // WorldLight v2: 108 bytes
    struct dworldlight_v2_t
    {
        Vector origin;
        Vector intensity;
        Vector normal;
        Vector shadow_offset;
        int32_t viscluster;
        int32_t type;
        int32_t style;
        float stopdot;
        float stopdot2;
        float exponent;
        float radius;
        float constant_attn;
        float linear_attn;
        float quadratic_attn;
        int32_t flags;
        int32_t texdata;
        int32_t parent;
        float unknown_1;            // New in v2
        float unknown_2;            // New in v2
    };
    static_assert(sizeof(dworldlight_v2_t) == 108, "TF2 dworldlight_v2_t must be 108 bytes");

    // WorldLight v3: 112 bytes (Apex v47 target format)
    struct dworldlight_v3_t
    {
        Vector origin;
        Vector intensity;
        Vector normal;
        Vector shadow_offset;
        int32_t viscluster;
        int32_t type;
        int32_t style;
        float stopdot;
        float stopdot2;
        float exponent;
        float radius;
        float constant_attn;
        float linear_attn;
        float quadratic_attn;
        int32_t flags;
        int32_t texdata;
        int32_t parent;
        float unknown_1;
        float unknown_2;
        float unknown_3;            // New in v3
    };
    static_assert(sizeof(dworldlight_v3_t) == 112, "TF2 dworldlight_v3_t must be 112 bytes");

    // LightProbe: 48 bytes (same as Apex v47 - includes padding)
    struct dlightprobe_t
    {
        int16_t ambientSH[12];          // 24 bytes
        int16_t skyDirSunVis[4];        // 8 bytes
        int8_t staticLightWeights[4];   // 4 bytes
        int16_t staticLightIndexes[4];  // 8 bytes
        int8_t pad[4];                  // 4 bytes
    };
    static_assert(sizeof(dlightprobe_t) == 48, "TF2 dlightprobe_t must be 48 bytes");

    // Vertex formats (TF2 vs Apex v47)

    // VertexUnlit: 20 bytes in both (but field interpretation differs)
    struct dvertex_unlit_t
    {
        uint32_t positionIndex;     // 4 bytes
        uint32_t normalIndex;       // 4 bytes
        float albedoUV[2];          // 8 bytes
        uint8_t color[4];           // 4 bytes (RGBA)
    };
    static_assert(sizeof(dvertex_unlit_t) == 20, "TF2 dvertex_unlit_t must be 20 bytes");

    // VertexLitFlat: 36 bytes in TF2, 20 bytes in Apex v47
    // Format: "2I2f4B4f" = position_index, normal_index, albedo_uv[2], colour[4], lightmap_uv[2], lightmap_step[2]
    struct dvertex_lit_flat_t
    {
        uint32_t positionIndex;     // 4 bytes
        uint32_t normalIndex;       // 4 bytes
        float albedoUV[2];          // 8 bytes
        uint8_t color[4];           // 4 bytes (RGBA)
        float lightmapUV[2];        // 8 bytes
        float lightmapStep[2];      // 8 bytes
    };
    static_assert(sizeof(dvertex_lit_flat_t) == 36, "TF2 dvertex_lit_flat_t must be 36 bytes");

    // VertexLitBump: 44 bytes in TF2, 32 bytes in Apex v47
    struct dvertex_lit_bump_t
    {
        uint32_t positionIndex;     // 4 bytes
        uint32_t normalIndex;       // 4 bytes
        float albedoUV[2];          // 8 bytes
        uint8_t color[4];           // 4 bytes
        float lightmapUV[2];        // 8 bytes
        float lightmapStep[2];      // 8 bytes - REMOVED in Apex (becomes part of unknown)
        int32_t tangent[2];         // 8 bytes - REMOVED in Apex
    };
    static_assert(sizeof(dvertex_lit_bump_t) == 44, "TF2 dvertex_lit_bump_t must be 44 bytes");

    // VertexUnlitTS: 28 bytes in TF2, 24 bytes in Apex v47
    struct dvertex_unlit_ts_t
    {
        uint32_t positionIndex;     // 4 bytes
        uint32_t normalIndex;       // 4 bytes
        float albedoUV[2];          // 8 bytes
        uint8_t color[4];           // 4 bytes
        int32_t tangent[2];         // 8 bytes - Reduced to unknown[2] in Apex
    };
    static_assert(sizeof(dvertex_unlit_ts_t) == 28, "TF2 dvertex_unlit_ts_t must be 28 bytes");

    // VertexBlinnPhong: 92 bytes in TF2, 24 bytes in Apex v47
    struct dvertex_blinn_phong_t
    {
        uint32_t positionIndex;     // 4 bytes
        uint32_t normalIndex;       // 4 bytes
        uint8_t color[4];           // 4 bytes
        float albedoUV[2];          // 8 bytes
        float lightmapUV[2];        // 8 bytes
        float tangent[16];          // 64 bytes - Major reduction in Apex
    };
    static_assert(sizeof(dvertex_blinn_phong_t) == 92, "TF2 dvertex_blinn_phong_t must be 92 bytes");

    // Static prop v13 (TF2 uses v13)
    #pragma pack(push, 1)
    struct dstaticprop_v13_t
    {
        Vector origin;              // 12 bytes
        Vector angles;              // 12 bytes (pitch, yaw, roll as YZX)
        float scale;                // 4 bytes
        uint16_t modelName;         // 2 bytes - index into model_names
        uint8_t solidMode;          // 1 byte
        uint8_t flags;              // 1 byte
        uint16_t skin;              // 2 bytes
        uint16_t cubemap;           // 2 bytes
        float forcedFadeScale;      // 4 bytes
        Vector lightingOrigin;      // 12 bytes
        int8_t cpuLevel[2];         // 2 bytes (min, max)
        int8_t gpuLevel[2];         // 2 bytes (min, max)
        uint8_t diffuseModulation[4]; // 4 bytes (RGBA)
        uint16_t collisionFlags[2]; // 4 bytes (add, remove)
    };
    #pragma pack(pop)
    static_assert(sizeof(dstaticprop_v13_t) == 64, "TF2 dstaticprop_v13_t must be 64 bytes");

    // CM_GRID: 28 bytes
    struct dcmgrid_t
    {
        float scale;
        int32_t offsetX;
        int32_t offsetY;
        int32_t countX;
        int32_t countY;
        int32_t numStraddleGroups;
        int32_t firstBrushPlane;
    };
    static_assert(sizeof(dcmgrid_t) == 28, "TF2 dcmgrid_t must be 28 bytes");

    // CM_BRUSH: 32 bytes
    struct dcmbrush_t
    {
        Vector origin;
        uint8_t numNonAxialNoDiscard;
        uint8_t numPlaneOffsets;
        int16_t index;
        Vector extents;
        int32_t brushSideOffset;
    };
    static_assert(sizeof(dcmbrush_t) == 32, "TF2 dcmbrush_t must be 32 bytes");

    // Cell: 8 bytes (same in TF2 and Apex)
    struct dcell_t
    {
        int16_t numPortals;
        int16_t firstPortal;
        int16_t flags;
        int16_t leafWaterData;
    };
    static_assert(sizeof(dcell_t) == 8, "TF2 dcell_t must be 8 bytes");

    // CSMAABBNode: 32 bytes in TF2 (different bitfield layout from Apex)
    struct dcsmnode_t
    {
        Vector mins;
        uint8_t numChildren;
        uint8_t numObjRefs;
        uint16_t totalObjRefs;
        Vector maxs;
        uint16_t firstChild;
        uint16_t firstObjRef;
    };
    static_assert(sizeof(dcsmnode_t) == 32, "TF2 dcsmnode_t must be 32 bytes");

    // CellAABBNode: 32 bytes in TF2 (different from Apex)
    struct dcellnode_t
    {
        Vector origin;
        uint8_t numChildren;
        uint8_t numObjRefs;
        uint16_t totalObjRefs;
        Vector extents;
        uint16_t firstChild;
        uint16_t firstObjRef;
    };
    static_assert(sizeof(dcellnode_t) == 32, "TF2 dcellnode_t must be 32 bytes");

    // GameLump header
    struct dgamelump_t
    {
        int32_t id;
        uint16_t flags;
        uint16_t version;
        int32_t fileofs;
        int32_t filelen;
    };
    static_assert(sizeof(dgamelump_t) == 16, "TF2 dgamelump_t must be 16 bytes");
}
