// main.cpp : This file contains the 'main' function. Program execution begins and ends there.
//
#include "stdafx.h"
#include <CommandLine.h>
#include <binstream.h>
#include <bspfile.h>
#include <rmem.h>
#include <versions.h>

int main(int argc, char** argv)
{
    printf("bspconv - Copyright (c) %s, rexx\n", &__DATE__[7]);

    const CommandLine cmdline(argc, argv);

    if (argc < 2)
        Error("invalid usage; run 'bspconv <input.bsp> [-pack] [-dedi] [-out <outputDir>]'\n");

    if (!FILE_EXISTS(argv[1]))
        Error("couldn't find input file \"%s\"\n", argv[1]);

    const std::string bspPath = argv[1];
    std::string outputDir;
    bool packAllLumps = false;
    bool dediTarget = false; // -dedi -> target v47 (S3 dedicated server); default -> v51 (S21 client)

    // Parse command line arguments
    for (int i = 2; i < argc; i++)
    {
        if (strcmp(argv[i], "-pack") == 0)
        {
            packAllLumps = true;
        }
        else if (strcmp(argv[i], "-dedi") == 0)
        {
            dediTarget = true;
        }
        else if (strcmp(argv[i], "-out") == 0 && i + 1 < argc)
        {
            outputDir = argv[++i];
            // Ensure output directory ends with separator
            if (!outputDir.empty() && outputDir.back() != '/' && outputDir.back() != '\\')
                outputDir += '/';
        }
    }

    CIOStream bspIn;
    if (!bspIn.Open(bspPath, CIOStream::READ | CIOStream::BINARY))
        Error("failed to open BSP file \"%s\"\n", bspPath.c_str());

    const size_t fileSize = bspIn.GetSize();

    if (fileSize < sizeof(BSPHeader_t))
        Error("input file is too small (must be at least 0x%x bytes\n", sizeof(BSPHeader_t));

    // read full bsp file into buffer to pass to each version func
    char* const buf = new char[fileSize];
    bspIn.Read(buf, fileSize);

    // Close input file before we overwrite it
    bspIn.Close();

    ConvertBSP(bspPath, buf, packAllLumps, dediTarget, outputDir);
}