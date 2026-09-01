#include "stdafx.h"
#include "versions.h"
#include "rmem.h"
#include "bspfile.h"

// convert v51 lightprobes to v47
// lightprobe struct got smaller by 4 bytes in version 51 by removing the "pad" variable
// that was used to align the struct to 16 bytes to use SIMD operations for optimisation
// this function appends the bytes back to the struct to make it 16 bytes again
void ConvertLightProbes_v51(rmem& lumpbuf, char*& lumpData, size_t& lumpSize)
{
	const size_t numLightProbes = lumpSize / (sizeof(r5::v51::dlightprobe_t));
	const size_t newLumpSize = numLightProbes * sizeof(dlightprobe_t);

	// allocate buffer for the converted lump data
	char* const newLumpData = new char[newLumpSize];
	rmem newLumpBuf(newLumpData);

	const r5::v51::dlightprobe_t* const lightProbes = reinterpret_cast<r5::v51::dlightprobe_t*>(lumpbuf.getPtr());

	for (size_t j = 0; j < numLightProbes; ++j)
	{
		lumpbuf.seek(j * sizeof(r5::v51::dlightprobe_t), rseekdir::beg);

		newLumpBuf.write<r5::v51::dlightprobe_t>(lightProbes[j]);
		newLumpBuf.write<int>(0);
	}

	delete[] lumpData;

	lumpData = newLumpData;
	lumpSize = newLumpSize;
}

// contract v47-era 48-byte lightprobes to the v51 44-byte layout (inverse of
// ConvertLightProbes_v51). The 44-byte v51 probe is the 48-byte probe's first 44
// bytes (identical field layout); only the trailing 4-byte SIMD pad is dropped.
void ContractLightProbes_v51(rmem& lumpbuf, char*& lumpData, size_t& lumpSize)
{
	const size_t numLightProbes = lumpSize / (sizeof(dlightprobe_t)); // 48-byte source
	const size_t newLumpSize = numLightProbes * sizeof(r5::v51::dlightprobe_t); // 44-byte

	char* const newLumpData = new char[newLumpSize];
	rmem newLumpBuf(newLumpData);

	const dlightprobe_t* const lightProbes = reinterpret_cast<dlightprobe_t*>(lumpbuf.getPtr());

	for (size_t j = 0; j < numLightProbes; ++j)
	{
		// write only the v51 (44-byte) view of the 48-byte source probe, dropping pad[4].
		newLumpBuf.write<r5::v51::dlightprobe_t>(
			*reinterpret_cast<const r5::v51::dlightprobe_t*>(&lightProbes[j]));
	}

	delete[] lumpData;

	lumpData = newLumpData;
	lumpSize = newLumpSize;
}
