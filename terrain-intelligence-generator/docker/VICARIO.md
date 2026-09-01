# Building with Java VicarIO

This Docker image uses the Java-based VicarIO library for VICAR image format conversion. The Java version provides better image quality with proper dynamic range rescaling compared to the Python implementation.

## Failure mode: check the output file

VicarIO exits 0 even when it converts nothing, printing the Java exception on
stdout, so `set -e` and `&&` chains do not catch its failures. Check that the
output file exists. In particular, keyword syntax with only two arguments
(`vicario inp=input.vic out=output.png`) is read positionally and treats
`inp=input.vic` as a filename; use the positional form for two arguments.

## Obtaining vicario.jar

The image ships a prebuilt `vicario.jar`, so nothing needs to be built to use
`vicario` in the container.

### Build from Source

The VicarIO source is **not yet public** — there is no repository to clone at
this time, so building from source is not currently possible outside JPL. When
source access is available, the build is:

1. Build the FAT JAR (includes all dependencies) in the VicarIO source tree:
   ```bash
   mvn -U -Pshade clean install
   ```

2. Copy the JAR to the Docker build context:
   ```bash
   cp target/vicario-*-FAT.jar /path/to/tig/terrain-intelligence-generator/docker/vicario.jar
   ```

## Building the Docker Image

Once `vicario.jar` is in place:

```bash
cd terrain-intelligence-generator/docker
docker build -t terrain-intelligence-generator:latest .
```

## Why Java VicarIO?

The Java implementation provides:

- **Correct dynamic range handling**: Automatically rescales 16-bit VICAR images to 8-bit with `oform=byte rescale=true`
- **Better image quality**: Preserves full dynamic range during conversion
- **Native VICAR support**: Direct parsing of VICAR labels and binary data
- **Format flexibility**: Supports PNG, JPEG, TIFF output formats

The wrapper script automatically applies the correct rescaling parameters for standard 2-argument usage, which is positional — input first, output second:
```bash
vicario input.vic output.png
```

For advanced usage, pass parameters directly; keyword syntax is only parsed
when three or more parameters are given:
```bash
vicario inp=input.vic out=output.png format=png oform=byte rescale=true
```
