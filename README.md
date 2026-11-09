# Tab Converter

Intelligently merge guitar/bass tabs for Chapman Stick and other multi-string instruments with temporal note mapping and automatic hand separation.

## Features

- **Temporal note allocation & voice leading**: Maps notes to strings with horizontal fret-hand continuity and compact chord voicings
- **Structured multi-track score inputs**: Directly converts multi-track **MIDI (`.mid`)**, **MusicXML (`.musicxml`, `.xml`, `.mxl`)**, and **Guitar Pro (`.gp`)** files where Guitar and Bass tracks share an exact musical timeline
- **Articulation & technique preservation**: Preserves hammer-ons (`h`), pull-offs (`p`), slides (`/`, `\`), bends (`7b9`), vibrato (`~`), and dead notes (`x`), keeping connected legato notes on the same target string
- **Automatic hand separation**: Enforces configurable fret gaps between bass and melody parts to prevent physical hand collisions
- **Smart string selection**: Prioritizes bass strings for bass parts and melody strings for melody parts, relaxing fret bounds before resorting to octave transposition
- **Header-aware section alignment**: Aligns song sections (`Intro`, `Verse`, `Chorus`, `Bridge`, `Solo`, `Outro`) across files even when section counts differ, and synchronizes measure barlines (`|`)
- **Zero-config tuning inference**: Automatically detects or infers source tunings from standard bare-label internet tabs (`e| B| G| D| A| E|`, `G| D| A| E|`, Drop D, etc.) as well as octave-annotated tabs

## Installation

```bash
uv sync
```

## Usage

Merge bass and melody parts for Chapman Stick or similar instruments:

```bash
uv run tabconverter.py bass.tab guitar.tab -o stick.tab -c config.json
```

The `-c` (or `-t`/`--target-tuning`) option accepts a path to a JSON config file, the name of a bundled tuning from `tunings/`, a shorthand alias (`stick`, `stick12`, `guitar`, `dropd`, `dadgad`, `bass`, `bass5`), or inline comma-separated notes:

```bash
uv run tabconverter.py bass.tab guitar.tab -o stick.tab -c stick/12string/classic
uv run tabconverter.py guitar.tab -c dropd
uv run tabconverter.py guitar.tab -t D2,A2,D3,G3,A3,D4
```

List all bundled tunings:

```bash
uv run tabconverter.py --list-tunings
```

If `-o` is omitted, the converted/merged tab is printed to stdout.

With explicit source tunings (accepts presets, aliases, or comma-separated notes in either low-to-high or high-to-low order):

```bash
uv run tabconverter.py bass.tab guitar.tab \
  -o stick.tab \
  -c stick \
  -s bass \
  -s guitar
```

Convert a multi-track MIDI, MusicXML, or Guitar Pro (`.gp`) file directly (automatically skips percussion tracks and merges bass + melody tracks):

```bash
# Inspect tracks in a structured file
uv run tabconverter.py song.mid --list-tracks

# Merge all pitched tracks onto a 10-string Chapman Stick, wrapped to 80 columns
uv run tabconverter.py song.mid -c stick -o stick.tab --wrap 80

# Select specific tracks by 1-based index or name
uv run tabconverter.py song.gp --tracks 1,2 -c stick12 -o stick.tab
```

Verbosity levels:

```bash
# Basic info (file detection, sections found)
uv run tabconverter.py bass.tab guitar.tab -o stick.tab -c config.json -v

# Detailed note mapping (shows which notes map to which strings/frets)
uv run tabconverter.py bass.tab guitar.tab -o stick.tab -c config.json -vv

# Full debug output (includes line scanning details)
uv run tabconverter.py bass.tab guitar.tab -o stick.tab -c config.json -vvv
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
- **melody_strings** (optional): Array of string indices (0-based, into `target_tuning`) that form the melody group. If omitted, the tuning is split in half and the half with the higher average pitch is treated as the melody group.

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

Note labels may include octave numbers (`E2|`), omit them (`E|`), or use the
common lowercase high-string convention (`e|`). Duplicate labels (e.g. the two
E strings of a guitar) are resolved automatically regardless of whether a
declared tuning is listed high-to-low or low-to-high. When octave numbers are
omitted from the tab lines, standard guitar/bass octaves are inferred
automatically; you can override them with `-s`.

Text lines immediately preceding a tab section (e.g. `Chorus:`) are preserved
as section headers and used to align sections across multiple files.

## Development

Run the test suite with:

```bash
uv run --group dev pytest
```

## Limitations

- Notes that cannot be mapped even with octave shifting will appear as 'X'
- Legacy binary Guitar Pro files (`.gp3`–`.gp5`) require `pyguitarpro` (`pip install pyguitarpro`), whereas GP7/8 (`.gp`), MusicXML (`.musicxml`/`.mxl`), and MIDI (`.mid`) are supported natively with zero external dependencies