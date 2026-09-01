#include "stdafx.h"
#include "versions.h"

#include "binstream.h"
#include "stltools.h"

#include "rmem.h"
#include "bspfile.h"
#include "entity_partition.h"

bool FixEntityPartition(const std::string& partitionPath, const bool parseHeader)
{
	CIOStream inEntityPartition;
	if (!inEntityPartition.Open(partitionPath, CIOStream::READ | CIOStream::BINARY))
	{
		printf("%s: Failed to open entity partition file: '%s'\n", __FUNCTION__, partitionPath.c_str());
		return false;
	}

	std::string partitionBuffer;
	inEntityPartition.ReadString(partitionBuffer);

	// Close input file before we overwrite it
	inEntityPartition.Close();

	CEntityPartitionMgr partitionMgr;
	if (!partitionMgr.ParseFromBuffer(partitionBuffer.c_str(), parseHeader))
	{
		printf("%s: Failed to parse entity partition file: '%s'\n", __FUNCTION__, partitionPath.c_str());
		return false;
	}

	if (!partitionMgr.ConvertEntityPartition())
	{
		printf("%s: Failed to convert entity partition file: '%s'\n", __FUNCTION__, partitionPath.c_str());
		return false;
	}

	if (!partitionMgr.Write(partitionPath.c_str()))
	{
		printf("%s: Failed to write entity partition file: '%s'\n", __FUNCTION__, partitionPath.c_str());
		return false;
	}

	printf("Updated entity partition file: %s\n", partitionPath.c_str());
	return true;
}


// ---------------------------------------------------------------------------
// Sidecar casing (S3 dedi VPK lookup)
//
// Loader opens "%s.%.4x.bsp_lump" then the Source FS lowercases the path before a
// CASE-SENSITIVE VPK-dir lookup. RSX writes UPPERCASE %X (.006A). Passthrough
// lumps keep those names -> lookup miss -> split BSP fileofs=0 fallback reads the
// rBSP *header* as the lump (rcx=="rBSP") -> cell-BSP spatial AV on first query.
// Working maps all ship lowercase sidecars. Always normalize A-F hex to lower.
// ---------------------------------------------------------------------------
static std::string LumpSidecarPath(const std::string& bspPath, const int lumpIndex, const bool upper)
{
	return Format(upper ? "%s.%04X.bsp_lump" : "%s.%04x.bsp_lump", bspPath.c_str(), lumpIndex);
}

// Prefer lowercase path; fall back to uppercase if that is what is on disk (pre-normalize).
static std::string ResolveLumpSidecarPath(const std::string& bspPath, const int lumpIndex)
{
	const std::string lower = LumpSidecarPath(bspPath, lumpIndex, false);
	if (std::filesystem::exists(lower))
		return lower;
	const std::string upper = LumpSidecarPath(bspPath, lumpIndex, true);
	if (std::filesystem::exists(upper))
		return upper;
	return lower;
}

static void RemoveLumpSidecar(const std::string& bspPath, const int lumpIndex)
{
	// On Windows exists() is case-insensitive so one remove is enough; still try both
	// strings so a Linux dual-file edge case is cleaned.
	std::error_code ec;
	for (const bool upper : { false, true })
	{
		const std::string lp = LumpSidecarPath(bspPath, lumpIndex, upper);
		if (std::filesystem::exists(lp))
			std::filesystem::remove(lp, ec);
	}
}

// Rename any .bsp.XXXX.bsp_lump sibling whose hex digits contain A-F uppercase to lowercase.
// Windows case-only renames need a temp path (source and dest are the same file otherwise).
static void NormalizeLumpSidecarCasing(const std::string& bspPath)
{
	const fs::path bsp(bspPath);
	const std::string stem = bsp.filename().string(); // e.g. map.bsp
	const fs::path dir = bsp.parent_path().empty() ? fs::path(".") : bsp.parent_path();
	const std::string prefix = stem + ".";
	const std::string suffix = ".bsp_lump";

	std::error_code ec;
	if (!fs::exists(dir, ec))
		return;

	int renamed = 0;
	for (const auto& entry : fs::directory_iterator(dir, ec))
	{
		if (ec || !entry.is_regular_file())
			continue;
		std::string name = entry.path().filename().string();
		if (name.size() < prefix.size() + 4 + suffix.size())
			continue;
		if (name.compare(0, prefix.size(), prefix) != 0)
			continue;
		if (name.compare(name.size() - suffix.size(), suffix.size(), suffix) != 0)
			continue;

		const std::string hex = name.substr(prefix.size(), name.size() - prefix.size() - suffix.size());
		if (hex.size() != 4)
			continue;

		bool hasUpper = false;
		std::string hexLower = hex;
		for (char& c : hexLower)
		{
			if (c >= 'A' && c <= 'F')
			{
				hasUpper = true;
				c = static_cast<char>(c - 'A' + 'a');
			}
		}
		if (!hasUpper)
			continue;

		const fs::path src = entry.path();
		const fs::path dst = dir / (prefix + hexLower + suffix);
		const fs::path tmp = dir / (prefix + hexLower + ".bsp_lump.__casefix__");

		fs::rename(src, tmp, ec);
		if (ec)
		{
			printf("[lump-case] WARNING: %s -> tmp failed: %s\n", name.c_str(), ec.message().c_str());
			continue;
		}
		fs::rename(tmp, dst, ec);
		if (ec)
		{
			printf("[lump-case] WARNING: tmp -> %s failed: %s\n",
				(prefix + hexLower + suffix).c_str(), ec.message().c_str());
			// best-effort: leave tmp for the user rather than silent loss
			continue;
		}
		printf("[lump-case] %s -> %s\n", name.c_str(), (prefix + hexLower + suffix).c_str());
		renamed++;
	}
	if (renamed > 0)
	{
		printf("[lump-case] normalized %d uppercase sidecar name(s) to lowercase "
			"(S3 VPK lookup is case-sensitive after FS lowercasing)\n", renamed);
	}
}

bool GetEntityPartitionNames(const std::string& bspPath, std::vector<std::string>& vec)
{
	// Lowercase hex only -- see NormalizeLumpSidecarCasing.
	const std::string entityPartitionLump = ResolveLumpSidecarPath(bspPath, lumptype_t::LUMP_ENTITY_PARTITIONS);
	CIOStream read;

	if (!read.Open(entityPartitionLump, CIOStream::READ | CIOStream::BINARY))
	{
		printf("Failed to open entity partition lump\n");
		return false;
	}

	dentitypartitionheader_t ep;
	read.Read(ep);

	if (ep.ident != dentitypartitionheader_t::VERSION)
	{
		printf("Unrecognized header in Entity Partition lump in bsp; ident=%hd, expected=%hd\n",
			ep.ident, dentitypartitionheader_t::VERSION);
		return false;
	}

	/*const bool shouldHaveEntityLump =*/ read.Read<char>() /*== '*'*/;

	std::string str;

	if (read.ReadString(str))
	{
		vec = StringSplit(str, ' ');
		return true;
	}

	return false;
}

// newer versions of the game have an extra field in the BVH header, this field
// has to be removed in order for BVH to function correctly. decode all *coll#
// base64 strings, remove the field, re-encode the data.
//
// NOTE: if additional changes are found or made in the entity partitions,
// such as renamed keys or header changes, perform the conversion here!
void FixEntityPartitions(const std::string& bspPath)
{
	const std::string pathNoExtension = RemoveExtension(bspPath);
	std::vector<std::string> entityPartitionNames;

	if (GetEntityPartitionNames(bspPath, entityPartitionNames))
	{
		for (const std::string& name : entityPartitionNames)
		{
			const std::string partitionPath = Format("%s_%s.ent", pathNoExtension.c_str(), name.c_str());
			FixEntityPartition(partitionPath, true);
		}
	}
}

void WriteLump(const std::string& lumpPath, const char* const lumpData, const size_t lumpSize)
{
	printf("Writing lump to: \"%s\" size: %zu\n", lumpPath.c_str(), lumpSize);

	CIOStream outLump;
	if (outLump.Open(lumpPath, CIOStream::WRITE | CIOStream::BINARY))
		outLump.Write(lumpData, lumpSize);
	else
		Error("Failed to open file for writing: %s\n", lumpPath.c_str());
}

// gamelumps have an absolute file offset to their data which needs to be updated to their new file offset
void FixGameLumpOffset(rmem& lumpBuf, const int nextLumpWriteOffset, const bool isPacked)
{
	const int numGameLumps = lumpBuf.read<int>();

	if (numGameLumps != 1)
		Error("Expected 1 game lump but found %i\n", numGameLumps);

	r5::dgamelump_t* pGameLump = lumpBuf.get<r5::dgamelump_t>();

	// adjust file offset to be relative to new location in bsp
	static const int headerOffset = (sizeof(dgamelumpheader_t) + sizeof(r5::dgamelump_t));


	// if the file is not going to be packed, we need to write the offset to the gamelump data, which is
	// just sizeof(dgamelumpheader_t) + sizeof(dgamelump_t), else the packed file offsets needs to be written too
	if (isPacked)
		pGameLump->fileofs = nextLumpWriteOffset + headerOffset;
	else
		pGameLump->fileofs = headerOffset;
}

// LumpSumResult: the engine-derived expected size totals for the bulk lightmap
// data lumps, computed from the 0x53 lightmap header entries. Mirrors
// Mod_CheckSizeAndLoadLightmapDataSky / Mod_LoadLightmapDataRealTimeLights.
struct LightmapSums
{
	bool   valid;        // false if 0x53 was malformed (e.g. not multiple of 8)
	int    entryCount;
	bool   anyUnknown;   // any per-entry switch hit the FATAL default branch
	size_t sky;          // 0x62 expected bytes (sum of f(type, w, h))
	size_t skyCompressed;// 0x61 expected bytes (same, on compressedType)
	size_t rtlInRPak;    // 0x69 expected bytes (in-rpak; v3 += v15)
	size_t rtlOnDisk;    // 0x69 expected bytes (on-disk; v3 += v15 + v14/2)
};

// Per-entry uncompressed-sky byte cost gate: engine code is
//   if (isCompressed || (((type - 1) & 0xF6) == 0 && type != 2)) ...
// which means: for the UNCOMPRESSED path only types {1, 9, 10} are processed.
// Returns 0 for skipped types so they contribute nothing to the sum.
static size_t SkyEntryBytesForType(unsigned t, unsigned w, unsigned h)
{
	switch (t)
	{
	case 1u: case 10u:
		return (size_t)8 * w * h;
	case 4u: case 8u:
		return (size_t)32 * ((w + 3) >> 2) * ((h + 3) >> 2);
	case 5u:
		return (size_t)32 * (((unsigned)w + 4) / 5u) * (((unsigned)h + 4) / 5u);
	case 6u:
		return (size_t)32 * (((unsigned)w + 5) / 6u) * (((unsigned)h + 5) / 6u);
	case 7u:
		return (size_t)32 * ((w + 7) >> 3) * ((h + 7) >> 3);
	case 9u:
		return (size_t)12 * w * h;
	default:
		return 0; // signals "unknown"
	}
}

static LightmapSums ComputeLightmapSums(const std::string& bspBase)
{
	LightmapSums s = { false, 0, false, 0, 0, 0, 0 };
	const std::string lp = Format("%s.%04x.bsp_lump", bspBase.c_str(), 0x53);
	if (!std::filesystem::exists(lp))
		return s;
	const size_t sz = (size_t)std::filesystem::file_size(lp);
	if ((sz % 8) != 0)
		return s;
	std::vector<char> buf(sz);
	{
		std::ifstream f(lp, std::ios::binary);
		f.read(buf.data(), sz);
	}
	s.entryCount = (int)(sz / 8);
	s.valid = true;
	for (size_t i = 0; i < (size_t)s.entryCount; ++i)
	{
		const unsigned char* p = (const unsigned char*)&buf[i * 8];
		const unsigned type           = p[0];
		const unsigned compressedType = p[1];
		const unsigned w = (unsigned)p[4] | ((unsigned)p[5] << 8);
		const unsigned h = (unsigned)p[6] | ((unsigned)p[7] << 8);

		// SKY (0x62): gate via lmapType (skip 0, 2, and anything outside {1, 9, 10})
		const bool skyGate = (((type - 1u) & 0xF6u) == 0u) && (type != 2u);
		if (skyGate)
		{
			const size_t add = SkyEntryBytesForType(type, w, h);
			if (add == 0 && (type != 0 && type != 2))
				s.anyUnknown = true;
			s.sky += add;
		}

		// SKY_COMPRESSED (0x61): no gate; just take compressedType into the switch
		{
			const size_t add = SkyEntryBytesForType(compressedType, w, h);
			if (add == 0 && (compressedType != 0))
				s.anyUnknown = true;
			s.skyCompressed += add;
		}

		// RTL (0x69): same gate as SKY uncompressed
		if (skyGate)
		{
			const size_t v13 = (size_t)w * h;
			const size_t v14 = (size_t)((w + 3) & ~3u) * (size_t)((h + 3) & ~3u);
			const size_t v15 = 2 * (v14 + 2 * v13);
			s.rtlInRPak += v15;
			s.rtlOnDisk += v15 + (v14 >> 1);
		}
	}
	return s;
}

// The single 'sprp' dgamelump_t inside GAME_LUMP carries a version field that
// mirrors the BSP version (0x33 == v51, 0x34 == v52). When downgrading v52 to
// v51 this u16 must be rewritten to match the output BSP version.
//
// Returns the previous version u16, or -1 if the lump was malformed.
int RewriteGameLumpVersion(char* const lumpData, const size_t lumpSize, const unsigned short newVersion)
{
	if (!lumpData || lumpSize < sizeof(dgamelumpheader_t) + sizeof(r5::dgamelump_t))
		return -1;

	rmem buf(lumpData);
	buf.setBufferSize(lumpSize);

	const int numGameLumps = buf.read<int>();
	if (numGameLumps != 1)
	{
		printf("Warning: GAME_LUMP has %d entries, expected 1; not rewriting version field\n", numGameLumps);
		return -1;
	}

	r5::dgamelump_t* const pGameLump = buf.get<r5::dgamelump_t>();
	const int oldVersion = pGameLump->version;
	pGameLump->version = newVersion;
	return oldVersion;
}

// expand lightmap RTL data to full size by padding with null bytes
//
// When loaded from .bsp_lump, RTL lightmaps set a bool in CMaterialSystem that is used to change the way that lightmap 
// data is used in shaders. Since this tool combines the .bsp_lumps into one .bsp without adding the missing data that would be
// required from the original internal .bsp lump, that bool is not set and the data is invalid, causing black boxes to appear everywhere
void FixLightmapRTLSize(BSPHeader_t* pHdr, const bool isPacked)
{
	assert(pHdr);

	// we bump the version to 1 so that a check can be made in R5SDK to manually set that bool without having to try and automatically
	// identify invalid lightmap data
	if (isPacked && pHdr)
	{
		pHdr->lumps[LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS].version = 1;
		pHdr->lumps[LUMP_LIGHTMAP_DATA_SKY].version = 1;
	}
}

// Copy all map files to output directory
void CopyMapFilesToOutput(const std::string& srcBspPath, const std::string& outputDir)
{
	namespace fs = std::filesystem;

	fs::path srcPath(srcBspPath);
	fs::path srcDir = srcPath.parent_path();
	std::string mapName = srcPath.stem().string(); // e.g. "mp_rr_thunderdome"

	// Create output directory if it doesn't exist
	fs::create_directories(outputDir);

	printf("Copying map files to: %s\n", outputDir.c_str());

	// Iterate all files in source directory
	for (const auto& entry : fs::directory_iterator(srcDir))
	{
		if (!entry.is_regular_file())
			continue;

		std::string filename = entry.path().filename().string();

		// Check if this file belongs to our map (starts with map name)
		if (filename.find(mapName) == 0)
		{
			// Skip .client suffix files - use the renamed versions
			if (filename.find(".client") != std::string::npos)
				continue;

			fs::path destPath = fs::path(outputDir) / filename;

			try
			{
				fs::copy_file(entry.path(), destPath, fs::copy_options::overwrite_existing);
				printf("  Copied: %s\n", filename.c_str());
			}
			catch (const std::exception& e)
			{
				printf("  Failed to copy %s: %s\n", filename.c_str(), e.what());
			}
		}
	}
}

// The S3 dedi reads a fixed set of lump indices. -dedi keeps the UNION of known-good v47
// server maps and drops the rest. Convert from the CLIENT bsp -- newer server vis is not S3.
static bool IsServerLump(const int lumpIndex)
{
	switch (lumpIndex)
	{
	case LUMP_ENTITIES:                  // 0x00
	case LUMP_PLANES:                    // 0x01
	case LUMP_TEXDATA:                   // 0x02
	case LUMP_VERTEXES:                  // 0x03
	case LUMP_MODELS:                    // 0x0e
	case LUMP_TEXDATA_STRING_DATA:       // 0x0f
	case LUMP_CONTENTS_MASKS:            // 0x10
	case LUMP_SURFACE_PROPERTIES:        // 0x11
	case LUMP_BVH_NODES:                 // 0x12 collision
	case LUMP_BVH_LEAF_DATA:             // 0x13 collision
	case LUMP_PACKED_VERTICES:           // 0x14 collision
	case LUMP_ENTITY_PARTITIONS:         // 0x18
	case LUMP_GAME_LUMP:                 // 0x23 static props
	case LUMP_UNKNOWN_37:                // 0x25 VIS
	case LUMP_UNKNOWN_39:                // 0x27 VIS
	case LUMP_WORLD_LIGHTS:              // 0x36
	case LUMP_MESHES:                    // 0x50
	case LUMP_MATERIAL_SORT:             // 0x52
	case LUMP_TWEAK_LIGHTS:              // 0x55
	case LUMP_CELL_BSP_NODES:            // 0x6a vis
	case LUMP_CELLS:                     // 0x6b vis
	case LUMP_CELL_AABB_NODES:           // 0x77 vis
	case LUMP_OBJ_REFS:                  // 0x78 vis
	case LUMP_OBJ_REF_BOUNDS:            // 0x79 vis
	case LUMP_LEVEL_INFO:                // 0x7b
		return true;
	default:
		return false;
	}
}

// convert BSP between Respawn versions.
//
//   dediTarget=false -> v51 (Season 21 client, flags=1) -- default.
//   dediTarget=true  -> v47 (Season 3 dedicated server, flags=0) -- restores the
//                       original rexx v47 product: server-only lump strip,
//                       v121->v8 brush-model collision downgrade (ENTITIES lump +
//                       entity partitions), gamelump version passthrough.
void ConvertBSP(const std::string& bspPath, char* const bspBuf, const bool packAllLumps, const bool dediTarget, const std::string& outputDir)
{
	rmem buf(bspBuf);
	BSPHeader_t* const pHdr = buf.get<BSPHeader_t>();

	if (pHdr->ident != 'PSBr')
		Error("Input file had invalid magic (expected \"rBSP\")\n");

	const int currentVersion = pHdr->version;

	// Target selection. v47 dedi uses flags=0 (legacy S3 reader); v51 S21 uses flags=1.
	const int targetVersion = dediTarget ? BSPVERSION_S3 : BSPVERSION_S21;
	const int targetFlags   = dediTarget ? 0 : 1;

	// Determine working path - use output directory if specified
	std::string workingBspPath = bspPath;
	if (!outputDir.empty())
	{
		// Copy all map files to output directory first
		CopyMapFilesToOutput(bspPath, outputDir);

		// Update working path to point to output directory
		std::filesystem::path srcPath(bspPath);
		workingBspPath = outputDir + srcPath.filename().string();
		printf("Working on: %s\n", workingBspPath.c_str());
	}

	// RSX writes .bsp.XXXX.bsp_lump with UPPERCASE hex; S3 VPK lookup needs lowercase.
	// Run before any open/delete so every subsequent path is the lower form.
	NormalizeLumpSidecarCasing(workingBspPath);

	// Handle Titanfall 2 (v37) conversion separately - it's packed and needs different handling.
	// rbsp_37 lands a v47 product today; the final header bump to v51 happens here on its
	// return path. TODO(s21-tf2): collapse the v37 -> v47 -> v51 staging into a single
	// v37 -> v51 path inside rbsp_37.cpp once the v47 product is verified end-to-end.
	if (currentVersion == 37)
	{
		// Get file size from the BSP - we need to find the largest lump end offset
		size_t fileSize = sizeof(BSPHeader_t);
		for (int i = 0; i < LUMP_COUNT; i++)
		{
			size_t lumpEnd = pHdr->lumps[i].fileofs + pHdr->lumps[i].filelen;
			if (lumpEnd > fileSize)
				fileSize = lumpEnd;
		}

		printf("Detected Titanfall 2 BSP (v37), file size: %zu bytes\n", fileSize);
		if (!ConvertFromTF2(workingBspPath, bspBuf, fileSize, packAllLumps))
			Error("Failed to convert Titanfall 2 BSP\n");

		// rbsp_37 has produced a v47 file on disk.
		if (dediTarget)
		{
			// v47 IS the dedi target - nothing more to do.
			printf("[ConvertBSP] TF2 v37 -> v47 (S3 dedi target) complete.\n");
		}
		else
		{
			// STUB-OBSERVABLE: the v47 -> v51 bump for the S21 client target is not
			// yet exercised end-to-end against an S21 engine.
			printf("[ConvertBSP] STUB: post-TF2 v47 -> v51 header bump not yet implemented; output will still report version=47\n");
		}
		return;
	}

	CIOStream out;
	if (!out.Open(workingBspPath, CIOStream::WRITE | CIOStream::BINARY))
		Error("Failed to write output BSP file; insufficient rights?\n");

	if(packAllLumps) // seek to end of header as we write lump data past it
		out.Seek(sizeof(BSPHeader_t));

	// v51 flags=1 (S21). v47 flags=0 (S3); flags=1 on v47 reads as version 65587.
	const int prevFlags = pHdr->flags;
	pHdr->version = targetVersion;
	pHdr->flags = targetFlags;

	printf("[ConvertBSP] source version: v%d  -> target: v%d (%s)  flags=0x%04x -> 0x%04x\n",
		currentVersion, targetVersion, dediTarget ? "S3 dedi" : "S21 client", prevFlags, pHdr->flags);

	// -dedi: drop every lump the dedicated server does not read. Also deletes the .bsp_lump sibling.
	if (dediTarget)
	{
		for (int slot = 0; slot < LUMP_COUNT; slot++)
		{
			if (IsServerLump(slot))
				continue;

			lump_t& L = pHdr->lumps[slot];
			const bool had = (L.filelen != 0 || L.fileofs != 0 || L.version != 0 || L.uncompLen != 0);
			L.fileofs = 0; L.filelen = 0; L.version = 0; L.uncompLen = 0;

			RemoveLumpSidecar(workingBspPath, slot);
			if (had)
				printf("[dedi-strip] dropped non-server lump 0x%02x\n", slot);
		}
	}

	// Entity partition v121 -> v8 brush-model collision downgrade. The v47 (dedi)
	// target needs it (v8 is the S3 on-disk collision form); the v51 (S21) target
	// keeps v121 (it IS the v51 form). v47-era sources are already v8, so only run
	// when the source is v48+.
	const bool runEntityPartitionDowngrade = dediTarget;
	if (runEntityPartitionDowngrade && currentVersion >= 48)
		FixEntityPartitions(workingBspPath);

	// v52 -> v51 header pre-pass: v52 stores data in lump slots that v51 considers
	// UNUSED. Observed populated in v52 reference maps:
	//   0x39 LUMP_UNUSED_57 (the canonical "new in v52" lump)
	//   0x3A LUMP_UNUSED_58
	//   0x3B LUMP_UNUSED_59
	// Clear them at the header level AND delete any on-disk .bsp_lump siblings so
	// the v51 engine does not try to read garbage. Failing to clear leaves an
	// orphan entry pointing at an absent file - some v52 maps hit this with
	// 0x39 (len=251) and 0x3B (len=140) referenced but no .bsp_lump on disk.
	if (!dediTarget && currentVersion >= 52)
	{
		static const int kV52OnlySlots[] = { 0x39, 0x3A, 0x3B };
		for (const int slot : kV52OnlySlots)
		{
			lump_t& L = pHdr->lumps[slot];
			if (L.filelen != 0 || L.fileofs != 0 || L.version != 0 || L.uncompLen != 0)
			{
				printf("[v52-clear] LUMP_UNUSED_%d (0x%02x): ofs=%d len=%d ver=%d -> 0/0/0\n",
					slot, slot, L.fileofs, L.filelen, L.version);
				L.fileofs = 0;
				L.filelen = 0;
				L.version = 0;
				L.uncompLen = 0;
			}
			const std::string lp = LumpSidecarPath(workingBspPath, slot, false);
			if (std::filesystem::exists(lp) || std::filesystem::exists(LumpSidecarPath(workingBspPath, slot, true)))
			{
				RemoveLumpSidecar(workingBspPath, slot);
				printf("[v52-clear] removed sidecar for lump 0x%02x\n", slot);
			}
		}
	}

	// v52 -> v51 lightmap fix. The v51 engine reads these lumps with an
	// 8-byte header stride (bsp_tool agrees).
	//
	// Engine readers and what they enforce:
	//   0x53 Mod_LoadLightmapHeaders                  stride 8B/entry,
	//          {u8 type; u8 compressedType; u8 tag; u8 pad; u16 width; u16 height}
	//          aborts on size % 8 != 0
	//   0x62 Mod_CheckSizeAndLoadLightmapDataSky      iterates the 0x53 entries,
	//          sums f(type, w, h) per entry (switch on type 1/4/5/6/7/8/9/10),
	//          requires the sum to EXACTLY equal lump size, else
	//          "Sky lightmaps exist, but are too large/small."
	//   0x61 same loader with isCompressed=true       same sum check using
	//          compressedType byte
	//   0x69 Mod_LoadLightmapDataRealTimeLights       same iterate-and-sum
	//          check; mismatch errors with
	//          "Odd LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS lump size..."
	//   0x7A Mod_LoadLightmapDataRealTimeLightPages   stride 126B/page,
	//          aborts on size % 126 != 0, also asserts 0x69 was loaded
	//          (lmapRealTimeLightsBytes != null), so disabling 0x69 forces
	//          disabling 0x7A too.
	//
	// Why we don't pad 0x53 (8 -> 16): the engine reads 8-byte stride at every
	// version. fr v51's 16-byte 0x53 is just TWO entries; fr v52's 8-byte is
	// ONE entry; district v52's 32-byte is FOUR entries. An 8 -> 16 pad would
	// invent fake all-zero second entries the engine would then walk and
	// type-switch on, breaking the load.
	//
	// Why we don't pass through 0x62/0x69 (the recent passthrough flip was
	// wrong): v52's 0x53 ships all zeros, so type=0 makes the engine SKIP
	// every entry, sum to zero, then compare zero against the multi-MB lump
	// size and abort with "too large". The 4x/3x byte ratio between 0x61/0x62/
	// 0x69 is a coincidence of texel count, not proof of compatibility.
	//
	// Lossless preservation of v52 lightmaps would require synthesising a
	// 0x53 whose per-entry f(type, w, h) sum matches each bulk lump size
	// exactly. That is a separate feature -- TODO.
	if (!dediTarget && currentVersion >= 52)
	{
		// 0x53 LIGHTMAP_HEADERS: pass through unchanged. The earlier 8 -> 16
		// pad inside this codebase has been retired; logging the no-op for
		// observability.
		{
			const std::string lp = Format("%s.%04x.bsp_lump", workingBspPath.c_str(), 0x53);
			if (std::filesystem::exists(lp))
			{
				const size_t sz = (size_t)std::filesystem::file_size(lp);
				printf("[v52-lightmap] LUMP_LIGHTMAP_HEADERS (0x53) passthrough %zu bytes (%zu entries @ 8B); engine reads 8B/entry\n",
					sz, sz / 8);
			}
		}

		// 0x61/0x62/0x69/0x7A: math-validated passthrough vs disable.
		// Walk the 0x53 entries, sum the per-entry bytes the engine will
		// expect, and compare to actual lump sizes. If they line up to the
		// byte, the v52 dump is honest (rsx export was correct) and we ship
		// it; otherwise we disable to avoid the "too large / too small"
		// engine aborts.
		const LightmapSums sums = ComputeLightmapSums(workingBspPath);
		auto lumpFileSize = [&](int slot) -> size_t {
			const std::string lp = Format("%s.%04x.bsp_lump", workingBspPath.c_str(), slot);
			return std::filesystem::exists(lp) ? (size_t)std::filesystem::file_size(lp) : 0;
		};
		auto headerFileLen = [&](int slot) -> size_t {
			return (size_t)pHdr->lumps[slot].filelen;
		};
		const size_t sz61 = lumpFileSize(0x61);
		const size_t sz62 = lumpFileSize(0x62);
		const size_t sz69 = lumpFileSize(0x69);
		const size_t sz7A = lumpFileSize(0x7A);
		// 0x69 has TWO valid sums (in-rpak vs on-disk). Accept either: in-rpak
		// matches the on-disk extracted file (Apex's behaviour: file holds the
		// in-rpak texel bytes, header records the on-disk padded size).
		const bool ok61 = (sz61 == 0) || (sums.valid && sz61 == sums.skyCompressed);
		const bool ok62 = (sz62 == 0) || (sums.valid && sz62 == sums.sky);
		const bool ok69File = (sz69 == 0) || (sums.valid && (sz69 == sums.rtlInRPak || sz69 == sums.rtlOnDisk));
		const bool ok69Hdr  = (headerFileLen(0x69) == 0) || (sums.valid && (headerFileLen(0x69) == sums.rtlOnDisk || headerFileLen(0x69) == sums.rtlInRPak));
		const bool ok7A = (sz7A == 0) || ((sz7A % 126) == 0);

		printf("[v52-lightmap-validate] 0x53 entries=%d valid=%d any-unknown-type=%d\n",
			sums.entryCount, sums.valid, sums.anyUnknown);
		printf("[v52-lightmap-validate]   expected sky=%zu skyComp=%zu rtl(rpak)=%zu rtl(disk)=%zu\n",
			sums.sky, sums.skyCompressed, sums.rtlInRPak, sums.rtlOnDisk);
		printf("[v52-lightmap-validate]   actual   sky=%zu(0x62) skyComp=%zu(0x61) rtl=%zu(0x69 file) rtl-hdr=%zu(0x69 hdr) pages=%zu(0x7A)\n",
			sz62, sz61, sz69, headerFileLen(0x69), sz7A);

		const bool allValid = sums.valid && !sums.anyUnknown && ok61 && ok62 && ok69File && ok69Hdr && ok7A;

		if (allValid)
		{
			printf("[v52-lightmap-validate] PASSTHROUGH (0x53/0x61/0x62/0x69/0x7A all match engine formulas)\n");
		}
		else
		{
			printf("[v52-lightmap-validate] DISABLE: ok61=%d ok62=%d ok69File=%d ok69Hdr=%d ok7A=%d sumsValid=%d unknownType=%d\n",
				ok61, ok62, ok69File, ok69Hdr, ok7A, sums.valid, sums.anyUnknown);
			static const struct { int slot; const char* name; } kDisableSlots[] = {
				{ 0x61, "LIGHTMAP_DATA_SKY_COMPRESSED" },
				{ 0x62, "LIGHTMAP_DATA_SKY" },
				{ 0x69, "LIGHTMAP_DATA_REAL_TIME_LIGHTS" },
				{ 0x7A, "LIGHTMAP_DATA_RTL_PAGES" },
			};
			for (const auto& d : kDisableSlots)
			{
				lump_t& L = pHdr->lumps[d.slot];
				if (L.filelen != 0 || L.fileofs != 0 || L.version != 0 || L.uncompLen != 0)
				{
					printf("[v52-lightmap-disable] LUMP_%s (0x%02x): ofs=%d len=%d ver=%d -> 0/0/0\n",
						d.name, d.slot, L.fileofs, L.filelen, L.version);
					L.fileofs = 0;
					L.filelen = 0;
					L.version = 0;
					L.uncompLen = 0;
				}
				const std::string lp = Format("%s.%04x.bsp_lump", workingBspPath.c_str(), d.slot);
				if (std::filesystem::exists(lp))
				{
					std::error_code ec;
					std::filesystem::remove(lp, ec);
					if (!ec)
						printf("[v52-lightmap-disable] removed %s\n", lp.c_str());
				}
			}
		}
	}

	const int numLumps = pHdr->lastLump + 1;
	std::vector<lump_t> lumps(numLumps);

	// copy lump info from header into vector so it can be sorted by offset
	memcpy_s(lumps.data(), numLumps * sizeof(lump_t), &pHdr->lumps, numLumps * sizeof(lump_t));

	// write lump index into uncompLen so that it can be accessed after sorting
	// uncompLen should be unused (and have no existing values from file)
	// leaving this var set isn't a problem because the "lumps" vector isn't written to file
	for (int i = 0; i < lumps.size(); ++i)
	{
		lumps[i].uncompLen = i;
	}

	// sort by lump offset
	std::sort(lumps.begin(), lumps.end());

	int nextLumpWriteOffset = sizeof(BSPHeader_t);

	for (const lump_t& lump : lumps)
	{
		if (lump.filelen == 0)
			continue;

		// retrieve lump index from temp storage in uncompLen
		const int i = lump.uncompLen;

		// e.g. mp_rr_box.bsp.007f.bsp_lump -- always lowercase (see NormalizeLumpSidecarCasing)
		const std::string lumpPath = LumpSidecarPath(workingBspPath, i, false);

		// make sure the lump file actually exists
		if (!std::filesystem::exists(lumpPath))
		{
			printf("Lump %04x file not found: %s\n", i, lumpPath.c_str());
			continue;
		}

		size_t lumpSize = GetFileSize(lumpPath);

		if (int(lumpSize) != lump.filelen)
			printf("Lump %04x file size mismatch (file %i, bsp %i)\n", i, int(lumpSize), lump.filelen);

		CIOStream lumpIn;

		if (!lumpIn.Open(lumpPath, CIOStream::READ | CIOStream::BINARY))
		{
			printf("Failed to open lump \"%s\"\n", lumpPath.c_str());
			continue;
		}

		char* lumpData = new char[lumpSize];
		lumpIn.Read(lumpData, lumpSize);

		// Close input file before we potentially overwrite it
		lumpIn.Close();

		size_t lumpOffset = 0;

		switch (i)
		{
		case LUMP_ENTITIES:
		{
			// The worldspawn ENTITIES lump (0x00) also carries *coll# brush models.
			// v51 (S21) target keeps the v121 layout (it IS the v51 form). v47 (dedi)
			// target must downgrade them v121 -> v8 (size-preserving: removes 8 bytes
			// of v12.1-only header fields, pads elsewhere for BVH SIMD alignment) -
			// the S3 server reads the v8 brush-model header, so leaving v121 here
			// misaligns every worldspawn collision offset by 8 bytes. This restores
			// the original rexx v47 behaviour (kral's server map confirms: ENTITIES
			// brush-model offsets are exactly 8 bytes smaller than the v121 source).
			// Non-fatal: a parse failure falls back to passthrough (never corrupts).
			if (dediTarget && currentVersion >= 48)
			{
				CEntityPartitionMgr entMgr;
				if (entMgr.ParseFromBuffer(lumpData, false) && entMgr.ConvertEntityPartition())
				{
					std::string outBuf;
					entMgr.WriteToString(outBuf);
					// The brush-model downgrade is byte-for-byte size preserving (removes
					// 8 header bytes, pads 8 for BVH SIMD alignment). WriteToString may drop
					// the single trailing '\0' terminator, so copy the converted head over
					// the buffer and leave the original tail byte(s) intact - keeps lumpSize
					// unchanged. (Matches the original rexx v47 behaviour.)
					if (outBuf.size() <= lumpSize)
						memcpy(lumpData, outBuf.data(), outBuf.size());
					else
						printf("[ENTITIES] WARNING: converted size %zu > %zu; keeping original\n", outBuf.size(), lumpSize);
				}
				else
					printf("[ENTITIES] WARNING: v121->v8 parse/convert failed; passing through unchanged\n");
			}

			if (!packAllLumps)
				WriteLump(lumpPath, lumpData, lumpSize);

			break;
		}
		case LUMP_GAME_LUMP:
		{
			// Two rewrites in this lump:
			//   1) FixGameLumpOffset: rebase the absolute fileofs (existing behavior).
			//   2) RewriteGameLumpVersion: the dgamelump_t.version u16 mirrors the
			//      BSP version. For a v51 output it must be 0x0033 regardless of
			//      whether the source was v52 (0x0034) or v51 (already 0x0033).
			rmem lumpBuf(lumpData);
			lumpBuf.setBufferSize(lumpSize);
			FixGameLumpOffset(lumpBuf, nextLumpWriteOffset, packAllLumps);

			// The sprp dgamelump_t.version is COSMETIC to the runtime: the S3 static-prop
			// loader finds the gamelump by its 'sprp' 4cc id and
			// reads only filelen - it never reads the version field. StaticPropLump_t is
			// 64 bytes on both S3 and v52, so the prop data parses identically regardless of version.
			// We canonicalise to 0x33 (the value every working legacy v47 map ships, and
			// what the v51 client wants) for both targets; kral's passthrough 0x34 is
			// equally valid to the engine. Neither value affects static-prop loading -
			// kral's StaticPropBoundsCheck hack guards a separate NULL render-array case.
			const unsigned short sprpVersion = (unsigned short)BSPVERSION_S21; // 0x33, cosmetic
			const int oldVer = RewriteGameLumpVersion(lumpData, lumpSize, sprpVersion);
			if (oldVer >= 0 && oldVer != sprpVersion)
				printf("[GAME_LUMP] dgamelump_t.version: 0x%04x -> 0x%04x\n", oldVer, sprpVersion);

			if (!packAllLumps)
				WriteLump(lumpPath, lumpData, lumpSize);

			break;
		}
		case LUMP_LIGHTPROBES:
		{
			// Probe stride shrank 48 -> 44 bytes at v51 (the trailing 4-byte SIMD
			// pad was dropped). `r5::v51::dlightprobe_t` is the 44-byte compact form;
			// `dlightprobe_t` is the 48-byte padded (<= v50 / v47) form. The TARGET
			// dictates the on-disk stride:
			//   v51 (S21):  want 44B  -> pass 44B through; contract 48B (STUB)
			//   v47 (dedi): want 48B  -> EXPAND 44B via ConvertLightProbes_v51;
			//                            pass 48B through
			const size_t SZ_PAD48 = sizeof(dlightprobe_t);               // 48
			const size_t SZ_V51   = sizeof(r5::v51::dlightprobe_t);      // 44
			const bool divides48 = (lumpSize % SZ_PAD48 == 0);
			const bool divides44 = (lumpSize % SZ_V51 == 0);

			if (dediTarget)
			{
				// v47 target wants 48-byte padded probes.
				if (currentVersion >= 51 && divides44)
				{
					// 44-byte compact source -> expand to 48-byte (adds 4-byte pad/probe).
					// ConvertLightProbes_v51 reallocs lumpData and updates lumpSize.
					rmem lpbuf(lumpData);
					ConvertLightProbes_v51(lpbuf, lumpData, lumpSize);
					printf("[LightProbes] expanded 44B -> 48B for v47 dedi target (new size=%zu)\n", lumpSize);
				}
				else
				{
					// already 48-byte (v47-era source) or indivisible -> pass through.
					printf("[LightProbes] v47 dedi passthrough (size=%zu, v=%d)\n", lumpSize, currentVersion);
				}
				if (!packAllLumps)
					WriteLump(lumpPath, lumpData, lumpSize);
			}
			else if (divides44)
			{
				// S21 v51 target: compact layout; pass through.
				if (!packAllLumps)
					WriteLump(lumpPath, lumpData, lumpSize);
			}
			else if (divides48 && currentVersion < 51)
			{
				// v47-era/48-byte padded layout into a v51 target: contract to the
				// 44-byte v51 probe (drop the trailing SIMD pad). Inverse of the dedi
				// expansion above. ConvertLightProbes_v51's counterpart updates lumpSize.
				rmem lpbuf(lumpData);
				ContractLightProbes_v51(lpbuf, lumpData, lumpSize);
				printf("[LightProbes] contracted 48B -> 44B for v51 client target (new size=%zu)\n", lumpSize);
				if (!packAllLumps)
					WriteLump(lumpPath, lumpData, lumpSize);
			}
			else
			{
				printf("[LightProbes] Warning: lump_size=%zu does not divide cleanly by 44 or 48 (v=%d); passing through\n",
					lumpSize, currentVersion);
				if (!packAllLumps)
					WriteLump(lumpPath, lumpData, lumpSize);
			}

			break;
		}
		case LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS:
		{
			// The BSP header records the PADDED in-memory size (here 3354624)
			// while the .bsp_lump file on disk holds only the unpadded actual
			// bytes (3096576). The engine pads with zeros at load time.
			//
			// In unpacked mode we must NOT touch the file on disk - it was
			// already copied verbatim by CopyMapFilesToOutput - and we set
			// `lumpSize = lump.filelen` purely so the header field that gets
			// written back below preserves the padded value.
			//
			// (Calling WriteLump here with `lump.filelen` would over-read the
			// allocated buffer by the pad delta and produce a zero-byte file.)
			lumpSize = lump.filelen;
			// Intentionally do not write the lump. STUB: packed-mode emission
			// for this lump still over-reads the buffer if packAllLumps=true;
			// addressed in a separate change.
			if (packAllLumps)
				printf("[RTL] STUB: packed-mode emit of LUMP_LIGHTMAP_DATA_REAL_TIME_LIGHTS not implemented\n");

			break;
		}
		case LUMP_UNUSED_57: // present only on v52 inputs
		{
			// Clear the lump regardless of source version - on v51 the slot is
			// LUMP_UNUSED_57 and any payload would be misinterpreted by the
			// engine. We zero in-memory, truncate the external lump to 0, and
			// also zero the per-lump version field in the header below.
			if (lumpSize > 0)
			{
				printf("Clearing LUMP_UNKNOWN_57 (0x0039) for v51 output (input v%d)\n", currentVersion);
				memset(lumpData, 0, lumpSize);
				if (!packAllLumps)
					WriteLump(lumpPath, lumpData, 0);
				lumpSize = 0;
			}
			pHdr->lumps[i].version = 0;
			pHdr->lumps[i].uncompLen = 0;

			break;
		}
		default:
		{
			// For all other lumps (including water lumps 44-48), write them unchanged
			if (!packAllLumps)
				WriteLump(lumpPath, lumpData, lumpSize);

			break;
		}
		}

		pHdr->lumps[i].fileofs = int(lumpOffset);
		pHdr->lumps[i].filelen = int(lumpSize);

		if (packAllLumps)
		{
			pHdr->lumps[i].fileofs = nextLumpWriteOffset;
			out.Write(lumpData, lumpSize);
			nextLumpWriteOffset += int(lumpSize);
		}

		delete[] lumpData;
	}

	// seek back to write the header
	if (packAllLumps)
		out.Seek(0);

	out.Write(pHdr, sizeof(BSPHeader_t));
}
