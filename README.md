# Tab Converter

Convert guitar/bass tabs between tunings and merge multiple tab files for Chapman Stick or other multi-string instruments.

## Installation

```bash
uv sync
```

## Usage

### Convert tabs to new tuning

```bash
# Auto-detect source tuning
uv run tab-converter convert input.tab output.tab -c config.json

# Specify source tuning explicitly
uv run tab-converter convert input.tab output.tab -c config.json -s E2 A2 D3 G3 B3 E4
```

### Merge multiple tab files

```bash
# Merge bass and melody tabs for Chapman Stick
uv run tab-converter merge bass.tab melody.tab -o stick.tab

# Merge any number of files
uv run tab-converter merge part1.tab part2.tab part3.tab -o combined.tab
```

## Configuration

The config file specifies the target tuning in JSON format:

```json
{
  "target_tuning": ["E2", "A2", "D3", "G3", "B3", "E4"]
}
```

Notes must include octave numbers for accurate pitch calculation.

## Tab File Format

Tab lines should follow this format:

```
E4|--5--7--9--|
B3|--5--7--8--|
G3|--6--7--9--|
```

## Chapman Stick 12-String Tuning

The included `config.json` contains the standard 36" scale Chapman Stick tuning:
- Bass strings (low to high): Bb1, Eb2, Ab2, C#3, F#3, B3
- Melody strings (high to low): Bb3, Eb4, Ab4, C#5, F#5, B5

When merging files, strings are automatically sorted by pitch (highest to lowest) for proper musical notation.
