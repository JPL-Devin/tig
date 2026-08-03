# GeoCal Integration Status

## Overview
This document tracks the status of integrating [GeoCal](https://github.com/Cartography-jpl/geocal) (geometric calibration and bundle adjustment) into the TIG environment.

## Background
- **GeoCal**: Part of the AFIDS (Automated Feature Identification for Downlink System) cartography suite from JPL
- **Purpose**: Advanced geometric calibration, bundle adjustment, camera modeling for planetary/Earth imaging missions
- **Relationship to TIG**: Both systems use VICAR as foundation; GeoCal adds modern geometric processing capabilities

## Current Status: **Build Complexity Blockers**

### Attempted Approaches

#### 1. ✗ Pre-built Conda Package
**Attempt**: Install `geocal` from conda-forge  
**Result**: Package does not exist in conda-forge  
**Notes**: AFIDS/GeoCal not published to public conda channels

#### 2. ✗ Build from Source (gcc-toolset-10)
**Attempt**: Install gcc-toolset-10 on Oracle Linux 8 for C++17 support  
**Result**: Build hung during 113MB+ toolset installation  
**Blocker**: Resource constraints, long build time

#### 3. ✗ Build from Source (micromamba + conda-forge compilers)
**Attempt**: Use micromamba + conda-forge's gxx_linux-64 for C++17 compiler  
**Result**: Successfully built CSPICE and Blitz++, but GeoCal configure failed  
**Blocker**: GeoCal's `configure.ac` has brittle GDAL version detection that fails with conda-installed GDAL:
```
checking version GDAL library is new enough... no
configure: error: Need to have GDAL >= 1.9.2
```
Even with GDAL 3.x installed and explicit `GDAL_CONFIG` path provided.

### Technical Challenges
1. **C++17 Requirement**: Oracle Linux 8 base has gcc 8.5 (too old), requires modern compiler
2. **Complex Dependencies**: CSPICE, Blitz++, Boost, GDAL, HDF5, VICAR RTL
3. **Autoconf Brittleness**: GeoCal's configure script has fragile dependency detection
4. **No Pre-built Binaries**: No official releases or conda packages available
5. **Build Time**: Multi-hour build expected (~45-60 min estimate was optimistic)

## Recommended Path Forward

### Option A: Wait for Upstream Improvements
- Request JPL/Cartography team publish conda packages or Docker images
- Wait for geocal CMake migration (more robust than autoconf)
- Track https://github.com/Cartography-jpl/geocal/issues

### Option B: Separate GeoCal Environment
Rather than integrating into TIG base image:
1. Create separate `tig-geocal-dev` image based on conda-forge/miniforge3
2. Install GeoCal from source with conda build tools
3. Install VICAR from TIG's pre-built binaries (extract from TIG image)
4. Link the two: use TIG for VICAR terrain processing, GeoCal for calibration
5. Provide data exchange scripts between environments

### Option C: Minimal GeoCal Build
Focus on subset of GeoCal functionality:
1. Build only core geocal library (no Python wrappers initially)
2. Skip GDAL integration for first pass (limits functionality but avoids configure issues)
3. Use as C++ library only, called from VICAR programs
4. Expand gradually as build issues resolved

### Option D: Document Integration for Users
Provide instructions for users to:
1. Clone geocal repo
2. Build locally (with detailed troubleshooting guide)
3. Mount into TIG container at runtime via `-v` bind mount
4. Set environment variables to link TIG VICAR + user's GeoCal

## Files Created
- `Dockerfile.geocal` - Multi-stage build attempt (incomplete/non-functional)
- `build-geocal-image.sh` - Build script (not tested end-to-end)
- `docs/geocal-integration.md` - Original integration documentation (optimistic)
- `docs/geocal-integration-status.md` - This status document

## Dependencies for Reference
From afids-conda-package analysis:
```yaml
# Core deps
- cspice >=N0067      # NASA SPICE toolkit
- blitz >=1.0.2       # C++ array library  
- boost-cpp
- gdal >=3.0
- hdf5
- gsl
- fftw
- sqlite

# Python deps  
- python >=3.9
- numpy
- scipy
- matplotlib
- pytest

# Build deps
- cmake >=3.18
- swig
- gcc >=11 (C++17)
- gfortran
```

## Next Steps
**Decision needed**: Which option (A, B, C, or D) to pursue?

**Recommendation**: Option B (Separate GeoCal Environment)
- Cleanest separation of concerns
- TIG stays lean and focused on VICAR terrain processing
- GeoCal environment can evolve independently
- Data exchange via files (natural boundary for both systems)
- Users who need both get both; users who only need TIG aren't burdened

