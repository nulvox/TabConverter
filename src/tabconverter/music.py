"""Pitch, semitone, and tuning utilities for TabConverter."""

import json
import re
from pathlib import Path


NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TUNINGS_DIR = (
    _REPO_ROOT / 'tunings'
    if (_REPO_ROOT / 'tunings').is_dir()
    else Path(__file__).resolve().parent / 'tunings'
)

# Ergonomic shorthand aliases for common bundled tunings
TUNING_ALIASES = {
    'guitar': 'guitar/6string/standard',
    'standard': 'guitar/6string/standard',
    '6string': 'guitar/6string/standard',
    'dropd': 'guitar/6string/dropD',
    'drop-d': 'guitar/6string/dropD',
    'dropc': 'guitar/6string/dropC',
    'drop-c': 'guitar/6string/dropC',
    'dadgad': 'guitar/6string/dadgad',
    'opend': 'guitar/6string/openD',
    'open-d': 'guitar/6string/openD',
    'openg': 'guitar/6string/openG',
    'open-g': 'guitar/6string/openG',
    'opene': 'guitar/6string/openE',
    'open-e': 'guitar/6string/openE',
    'openc': 'guitar/6string/openC',
    'open-c': 'guitar/6string/openC',
    'halfstep': 'guitar/6string/halfstepDown',
    'halfstepdown': 'guitar/6string/halfstepDown',
    'eb': 'guitar/6string/halfstepDown',
    'stepdown': 'guitar/6string/stepDown',
    'dstandard': 'guitar/6string/stepDown',
    'nashville': 'guitar/6string/nashville',
    'all4ths': 'guitar/6string/all4thsE',
    '7string': 'guitar/7string/standard',
    'guitar7': 'guitar/7string/standard',
    '8string': 'guitar/8string/standard',
    'guitar8': 'guitar/8string/standard',
    'bass': 'bass/4string/standard',
    'bass4': 'bass/4string/standard',
    '4string': 'bass/4string/standard',
    'bass5': 'bass/5string/standard',
    '5string': 'bass/5string/standard',
    'stick': 'stick/10string/classic',
    'stick10': 'stick/10string/classic',
    'stick12': 'stick/12string/classic',
    'stick8': 'stick/8string/classic',
}

TAB_LINE_PATTERN = re.compile(r'^([A-Ga-g][#b]?\d*)\s*\|')


def canonical_pitch_class(note_name):
    """Convert a bare note name (e.g. 'Bb', 'f#', 'e') to its canonical sharp name in NOTES."""
    note_name = note_name.strip()
    if not note_name:
        raise ValueError(f"Empty note name: {note_name!r}")
    base = note_name[0].upper()
    if base not in NOTES:
        raise ValueError(f"Invalid note: {note_name}")
    idx = NOTES.index(base)
    for acc in note_name[1:]:
        if acc == '#':
            idx = (idx + 1) % 12
        elif acc in ('b', 'B'):
            idx = (idx - 1) % 12
        else:
            raise ValueError(f"Invalid accidental in note: {note_name!r}")
    return NOTES[idx]


def note_to_semitones(note):
    """Convert note name to semitone offset from C0."""
    note = note.strip()
    match = re.match(r'^([A-Ga-g][#b]?)(\d+)$', note)
    if not match:
        raise ValueError(
            f"Invalid note format: {note!r} (expected e.g. 'E2', 'F#3', 'Bb1')")

    note_name, octave = match.groups()
    octave = int(octave)
    pitch_name = canonical_pitch_class(note_name)
    return NOTES.index(pitch_name) + (octave * 12)


def semitones_to_note(semitones):
    """Convert semitone offset to note name."""
    octave = semitones // 12
    note = NOTES[semitones % 12]
    return f"{note}{octave}"


def midi_to_semitones(midi_note):
    """Convert standard MIDI note number (where C4 = 60, C0 = 12) to TabConverter semitones (C0 = 0)."""
    return int(midi_note) - 12


def midi_to_note(midi_note):
    """Convert standard MIDI note number to TabConverter note string (e.g. 40 -> 'E2')."""
    return semitones_to_note(midi_to_semitones(midi_note))


def parse_tuning(tuning):
    """Parse tuning from list of note strings to semitone offsets."""
    return [note_to_semitones(note) for note in tuning]


def resolve_config_path(config_arg, tunings_dir=None):
    """Resolve a config argument to a file path.

    Accepts a path to a JSON file, a bundled tuning name (e.g.
    'stick/12string/classic', with or without '.json'), or a shorthand alias
    (e.g. 'stick', 'dropd', 'guitar', 'bass').
    """
    base_dir = tunings_dir if tunings_dir is not None else TUNINGS_DIR
    path = Path(config_arg)
    if path.is_file():
        return path

    name = str(config_arg).strip()
    alias_target = TUNING_ALIASES.get(name.lower())
    search_names = [name]
    if alias_target:
        search_names.append(alias_target)

    for candidate_name in search_names:
        for candidate in (base_dir / candidate_name, base_dir / f"{candidate_name}.json"):
            if candidate.is_file():
                return candidate

    available = ', '.join(list_bundled_tunings(base_dir)[:5])
    raise FileNotFoundError(
        f"Config not found: {config_arg!r}. Provide a JSON file path, a "
        f"bundled tuning name (e.g. {available}, ...), or a shorthand alias "
        f"(e.g. guitar, dropd, bass, stick). "
        f"Use --list-tunings to see all bundled tunings.")


def list_bundled_tunings(tunings_dir=None):
    """Return sorted names of all bundled tunings (relative to tunings/)."""
    base_dir = tunings_dir if tunings_dir is not None else TUNINGS_DIR
    if not base_dir.is_dir():
        return []
    return sorted(
        str(p.relative_to(base_dir).with_suffix(''))
        for p in base_dir.rglob('*.json'))


def load_config(config_arg, tunings_dir=None):
    """Load and validate target tuning config from a file path, tuning name, or inline note list."""
    if isinstance(config_arg, str) and not Path(config_arg).is_file():
        stripped_arg = config_arg.strip()
        if ',' in stripped_arg or re.match(r'^[A-Ga-g][#b]?\d+$', stripped_arg):
            notes = [n.strip() for n in stripped_arg.split(',') if n.strip()]
            if all(re.match(r'^[A-Ga-g][#b]?\d*$', n) for n in notes):
                if not any(re.search(r'\d', n) for n in notes):
                    notes = infer_octaves_for_bare_labels(notes, top_to_bottom=False)
                parse_tuning(notes)
                return {
                    'target_tuning': notes,
                    'max_fret': 24,
                    'bass_max_fret': 24,
                    'melody_min_fret': 0,
                    'hand_separation': 0,
                }

    config_path = resolve_config_path(config_arg, tunings_dir=tunings_dir)
    with open(config_path) as f:
        config = json.load(f)

    if 'target_tuning' not in config:
        raise ValueError(f"Config {config_path} must contain 'target_tuning' key")

    try:
        parse_tuning(config['target_tuning'])
    except ValueError as e:
        raise ValueError(f"Invalid target_tuning in {config_path}: {e}")

    for key in ('max_fret', 'bass_max_fret', 'melody_min_fret', 'hand_separation'):
        if key in config and not isinstance(config[key], int):
            raise ValueError(f"Config key {key!r} must be an integer, got {config[key]!r}")

    return config


def parse_source_tuning_arg(tuning_str, tunings_dir=None):
    """Parse a -s/--source-tuning CLI value.

    Accepts:
      - Comma- or space-separated notes with octaves ("E1,A1,D2,G2" or "E1 A1 D2 G2")
      - Comma- or space-separated bare notes ("E,A,D,G" -> octaves inferred)
      - A bundled tuning name ("bass/4string/standard") or alias ("bass", "guitar", "dropd")
    """
    raw = tuning_str.strip()
    if ',' not in raw and ' ' not in raw and not re.match(r'^[A-Ga-g][#b]?\d+$', raw):
        try:
            cfg = load_config(raw, tunings_dir=tunings_dir)
            return list(cfg['target_tuning'])
        except (FileNotFoundError, ValueError):
            pass

    tokens = [t.strip() for t in re.split(r'[,\s]+', raw) if t.strip()]
    if not tokens:
        raise ValueError(f"Empty source tuning: {tuning_str!r}")

    if all(re.match(r'^[A-Ga-g][#b]?\d+$', t) for t in tokens):
        parse_tuning(tokens)
        return tokens

    if all(re.match(r'^[A-Ga-g][#b]?$', t) for t in tokens):
        top_to_bottom = _is_likely_top_to_bottom_bare(tokens)
        return infer_octaves_for_bare_labels(tokens, top_to_bottom=top_to_bottom)

    parse_tuning(tokens)
    return tokens


def _is_likely_top_to_bottom_bare(labels):
    """Heuristic for whether bare note labels are ordered high-to-low (tab line order)
    vs low-to-high (tuning-list order)."""
    if not labels:
        return True
    if labels[0] == 'e' and labels[-1] == 'E':
        return True
    pitches = [NOTES.index(canonical_pitch_class(l)) for l in labels]
    n = len(pitches)
    if n == 4:
        asc_fourths = sum(1 for i in range(n - 1) if (pitches[i + 1] - pitches[i]) % 12 == 5)
        desc_fourths = sum(1 for i in range(n - 1) if (pitches[i] - pitches[i + 1]) % 12 == 5)
        if asc_fourths > desc_fourths:
            return False
        if desc_fourths > asc_fourths:
            return True
    if n >= 5:
        asc_fourths = sum(1 for i in range(n - 1) if (pitches[i + 1] - pitches[i]) % 12 in (4, 5))
        desc_fourths = sum(1 for i in range(n - 1) if (pitches[i] - pitches[i + 1]) % 12 in (4, 5))
        if asc_fourths > desc_fourths:
            return False
        if desc_fourths > asc_fourths:
            return True
    return True


def infer_octaves_for_bare_labels(labels, top_to_bottom=True, instrument_hint=None):
    """Infer octave numbers for a list of bare string labels (e.g. ['e', 'B', 'G', 'D', 'A', 'E'])."""
    if not labels:
        return []

    work_labels = list(labels) if top_to_bottom else list(reversed(labels))
    num_strings = len(work_labels)

    top_pc = NOTES.index(canonical_pitch_class(work_labels[0]))
    if instrument_hint == 'bass' or (instrument_hint is None and num_strings <= 5):
        start_octave = 2 if top_pc >= NOTES.index('C') else 3
    else:
        start_octave = 4 if top_pc <= NOTES.index('G') else 3

    result = []
    prev_semi = start_octave * 12 + top_pc
    result.append(f"{canonical_pitch_class(work_labels[0])}{start_octave}")

    for raw_label in work_labels[1:]:
        pc_name = canonical_pitch_class(raw_label)
        pc = NOTES.index(pc_name)
        candidate_oct = prev_semi // 12
        candidate_semi = candidate_oct * 12 + pc
        if candidate_semi >= prev_semi:
            candidate_oct -= 1
            candidate_semi -= 12
        result.append(f"{pc_name}{candidate_oct}")
        prev_semi = candidate_semi

    if not top_to_bottom:
        result.reverse()
    return result


def detect_source_tuning(tab_lines):
    """Extract source tuning from the first tab section of a file, if present.

    Only labels that include an octave number (e.g. 'E2|---') carry enough
    information to derive an explicit tuning directly.
    """
    tuning_pattern = re.compile(r'^([A-Ga-g][#b]?\d+)\s*\|')
    tuning = []

    for line in tab_lines:
        match = tuning_pattern.match(line.strip())
        if match:
            label = match.group(1)
            m = re.match(r'^([A-Ga-g][#b]?)(\d+)$', label)
            note_part, oct_part = m.groups()
            norm_note = note_part[0].upper() + note_part[1:]
            tuning.append(f"{norm_note}{oct_part}")
        elif tuning:
            break

    return tuning or None


def infer_source_tuning(tab_lines):
    """Infer source tuning from bare tab labels (e.g. 'e|', 'B|', 'G|') and optional preamble text."""
    bare_labels = []
    preamble_lines = []

    for line in tab_lines:
        stripped = line.strip()
        match = TAB_LINE_PATTERN.match(stripped)
        if match:
            bare_labels.append(match.group(1))
        elif bare_labels:
            break
        elif stripped:
            preamble_lines.append(stripped.lower())

    if not bare_labels:
        return None

    clean_bare = [re.sub(r'\d+', '', lbl) for lbl in bare_labels]
    preamble_text = ' '.join(preamble_lines)
    instrument_hint = None
    if 'bass' in preamble_text:
        instrument_hint = 'bass'
    elif 'guitar' in preamble_text:
        instrument_hint = 'guitar'

    top_to_bottom = _is_likely_top_to_bottom_bare(clean_bare)
    return infer_octaves_for_bare_labels(
        clean_bare, top_to_bottom=top_to_bottom, instrument_hint=instrument_hint
    )


def get_melody_strings(target_tuning, config):
    """Determine which target string indices belong to the melody group."""
    if 'melody_strings' in config:
        return set(config['melody_strings'])

    target_semi = parse_tuning(target_tuning)
    num_strings = len(target_semi)
    if num_strings < 2:
        return set(range(num_strings))

    mid = num_strings // 2
    first_half = target_semi[:mid]
    second_half = target_semi[mid:]
    if sum(first_half) / len(first_half) > sum(second_half) / len(second_half):
        return set(range(mid))
    return set(range(mid, num_strings))


def get_display_string_order(target_tuning, config):
    """Return target string indices ordered from top tab line to bottom tab line."""
    num_strings = len(target_tuning)
    if num_strings <= 1:
        return list(range(num_strings))

    if 'display_order' in config:
        return list(config['display_order'])

    target_semi = parse_tuning(target_tuning)
    melody_strings = get_melody_strings(target_tuning, config)

    if (
        0 in melody_strings
        and len(melody_strings) < num_strings
        and target_semi[0] > target_semi[min(melody_strings, key=lambda i: target_semi[i])]
    ):
        mel_indices = sorted(melody_strings, key=lambda i: target_semi[i], reverse=True)
        bass_indices = [i for i in range(num_strings) if i not in melody_strings]
        bass_indices_sorted = sorted(bass_indices, key=lambda i: target_semi[i], reverse=True)
        return mel_indices + bass_indices_sorted

    return list(reversed(range(num_strings)))


def is_split_hand_config(config):
    """Return True if the config enforces separate bass/melody hand regions (like Chapman Stick)."""
    if not config:
        return False
    max_fret = config.get('max_fret', 24)
    return (
        'melody_strings' in config
        or config.get('hand_separation', 0) > 0
        or config.get('melody_min_fret', 0) > 0
        or config.get('bass_max_fret', max_fret) < max_fret
    )
