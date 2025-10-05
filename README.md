# Tab Converter

Intelligently merge guitar/bass tabs for Chapman Stick and other multi-string instruments with temporal note mapping and automatic hand separation.

## Features

- **Temporal note allocation**: Maps notes to strings based on when they're played, not just pitch
- **Automatic hand separation**: Enforces configurable fret gaps between bass and melody parts to prevent hand collisions
- **Smart string selection**: Prioritizes bass strings for bass parts and melody strings for melody parts, with fallback options
- **Octave shifting**: Automatically transposes notes up/down octaves when needed to fit playable fret ranges
- **Collision avoidance**: Ensures no two parts use the same string at the same time
- **Auto-detection**: Detects source tunings and part types (bass/melody) automatically

## Installation

```bash
uv sync
```

## Usage

### Merge multiple tab files

Merge bass and melody parts for Chapman Stick or similar instruments:

```bash
uv run tabconverter.py merge bass.tab guitar.tab -o stick.tab -c config.json
```

With explicit source tunings:

```bash
uv run tabconverter.py merge bass.tab guitar.tab \
  -o stick.tab \
  -c config.json \
  -s E1,A1,D2,G2 \
  -s E2,A2,D3,G3,B3,E4
```

Verbosity levels:

```bash
# Basic info (file detection, sections found)
uv run tabconverter.py merge bass.tab guitar.tab -o stick.tab -c config.json -v

# Detailed note mapping (shows which notes map to which strings/frets)
uv run tabconverter.py merge bass.tab guitar.tab -o stick.tab -c config.json -vv

# Full debug output (includes line scanning details)
uv run tabconverter.py merge bass.tab guitar.tab -o stick.tab -c config.json -vvv
```

## Configuration

Config files specify target tuning and playability constraints. Example for Chapman Stick Matched Reciprocal tuning:

```json
{
  "target_tuning": ["E1", "A1", "D2", "G2", "C3", "F3", "F3", "C3", "G2", "D2", "A1", "E1"],
  "max_fret": 24,
  "bass_max_fret": 12,
  "melody_min_fret": 7,
  "hand_separation": 4
}
```

### Configuration Parameters

- **target_tuning**: Array of note names with octave numbers (low to high for stick, high to low for display)
- **max_fret**: Maximum fret number on the instrument (default: 24)
- **bass_max_fret**: Maximum fret for bass/left hand parts (default: 12)
- **melody_min_fret**: Minimum fret for melody/right hand parts (default: 7)
- **hand_separation**: Minimum fret gap between hands to prevent collisions (default: 4)

Popular tunings are included in `tunings/` directory.

## How It Works

The converter uses temporal note mapping rather than simple string-to-string conversion:

1. **Part detection**: Determines if each input file is bass (avg pitch < 30 semitones) or melody (≥ 30)
2. **Note extraction**: Parses all notes with their exact column positions (timestamps)
3. **Smart allocation**: For each timestamp, allocates notes to target strings considering:
   - String availability (no collisions)
   - Hand-specific fret ranges (bass: 0-12, melody: 7-24)
   - Hand separation (4-fret gap minimum)
   - String region preferences (bass prefers first 6 strings, melody prefers last 6)
4. **Octave shifting**: If a note won't fit, tries ±1 and ±2 octave shifts before marking as unplayable
5. **Two-pass allocation**: Bass notes get first pick of strings, then melody fills remaining slots

## Tab File Format

Input files should use standard tablature format with note labels:

```
G2|-------------------------------------------------------------|
D2|-------------------------------------------------------------|
A1|--0--0--1--1--2--2--0--0--0--0--1--1--2--2--3--3--2----------|
E1|-------------------------------------------------------------|
```

Note labels must match the source tuning (either with or without octave numbers). The converter auto-detects tunings from the note labels in the file.

## Limitations

- Notes that cannot be mapped even with octave shifting will appear as 'X'
- Source tunings must be detectable from the file or specified via `-s` flags
- All input files in a merge operation must have the same number of sections (or the tool will pad with empty sections)
