#pragma once
#include "rmem.h"

// Apex v48+ -> v47 conversion
void ConvertLightProbes_v51(rmem& lumpbuf, char*& lumpData, size_t& lumpSize);
// inverse of the above: contract v47-era 48-byte lightprobes to the v51 44-byte layout
// (drops the trailing 4-byte SIMD pad). Used for OLD(<=v50) -> v51 client up-converts.
void ContractLightProbes_v51(rmem& lumpbuf, char*& lumpData, size_t& lumpSize);
// dediTarget=false -> v51 (Season 21 client); dediTarget=true -> v47 (S3 dedicated server)
void ConvertBSP(const std::string& bspPath, char* const bspBuf, const bool packAllLumps, const bool dediTarget, const std::string& outputDir = "");

// Titanfall 2 v37 -> Apex v47 conversion
bool ConvertFromTF2(const std::string& bspPath, char* const bspBuf, const size_t fileSize, const bool packAllLumps);
