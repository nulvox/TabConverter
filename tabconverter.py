#!/usr/bin/env python3
"""Convert guitar/bass tabs between tunings and merge multiple tab files."""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

TUNINGS_DIR = Path(__file__).resolve().parent / 'tunings'

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


def parse_tuning(tuning):
    """Parse tuning from list of note strings to semitone offsets."""
    return [note_to_semitones(note) for note in tuning]


def resolve_config_path(config_arg):
    """Resolve a config argument to a file path.

    Accepts a path to a JSON file, a bundled tuning name (e.g.
    'stick/12string/classic', with or without '.json'), or a shorthand alias
    (e.g. 'stick', 'dropd', 'guitar', 'bass').
    """
    path = Path(config_arg)
    if path.is_file():
        return path

    name = str(config_arg).strip()
    alias_target = TUNING_ALIASES.get(name.lower())
    search_names = [name]
    if alias_target:
        search_names.append(alias_target)

    for candidate_name in search_names:
        for candidate in (TUNINGS_DIR / candidate_name, TUNINGS_DIR / f"{candidate_name}.json"):
            if candidate.is_file():
                return candidate

    available = ', '.join(list_bundled_tunings()[:5])
    raise FileNotFoundError(
        f"Config not found: {config_arg!r}. Provide a JSON file path, a "
        f"bundled tuning name (e.g. {available}, ...), or a shorthand alias "
        f"(e.g. guitar, dropd, bass, stick). "
        f"Use --list-tunings to see all bundled tunings.")


def list_bundled_tunings():
    """Return sorted names of all bundled tunings (relative to tunings/)."""
    if not TUNINGS_DIR.is_dir():
        return []
    return sorted(
        str(p.relative_to(TUNINGS_DIR).with_suffix(''))
        for p in TUNINGS_DIR.rglob('*.json'))


def load_config(config_arg):
    """Load and validate target tuning config from a file path, tuning name, or inline note list."""
    # Allow inline comma-separated tuning directly in -c (e.g. -c "D2,A2,D3,G3,A3,D4")
    if isinstance(config_arg, str) and ',' in config_arg and not Path(config_arg).is_file():
        notes = [n.strip() for n in config_arg.split(',') if n.strip()]
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

    config_path = resolve_config_path(config_arg)
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


def parse_source_tuning_arg(tuning_str):
    """Parse a -s/--source-tuning CLI value.

    Accepts:
      - Comma- or space-separated notes with octaves ("E1,A1,D2,G2" or "E1 A1 D2 G2")
      - Comma- or space-separated bare notes ("E,A,D,G" -> octaves inferred)
      - A bundled tuning name ("bass/4string/standard") or alias ("bass", "guitar", "dropd")
    """
    raw = tuning_str.strip()
    # Try bundled tuning or alias first if it doesn't look like a note list
    if ',' not in raw and ' ' not in raw and not re.match(r'^[A-Ga-g][#b]?\d+$', raw):
        try:
            cfg = load_config(raw)
            return list(cfg['target_tuning'])
        except (FileNotFoundError, ValueError):
            pass

    tokens = [t.strip() for t in re.split(r'[,\s]+', raw) if t.strip()]
    if not tokens:
        raise ValueError(f"Empty source tuning: {tuning_str!r}")

    # Check if tokens already have octave numbers
    if all(re.match(r'^[A-Ga-g][#b]?\d+$', t) for t in tokens):
        parse_tuning(tokens)
        return tokens

    # Check if tokens are bare note names (e.g. "E,A,D,G" or "e,B,G,D,A,E")
    if all(re.match(r'^[A-Ga-g][#b]?$', t) for t in tokens):
        # Determine whether the user wrote them high-to-low (e.g. starting with 'e' or G D A E)
        # or low-to-high (e.g. E A D G B E or E A D G)
        top_to_bottom = _is_likely_top_to_bottom_bare(tokens)
        return infer_octaves_for_bare_labels(tokens, top_to_bottom=top_to_bottom)

    # Otherwise validate directly to produce a clear error
    parse_tuning(tokens)
    return tokens


def _is_likely_top_to_bottom_bare(labels):
    """Heuristic for whether bare note labels are ordered high-to-low (tab line order)
    vs low-to-high (pedal/tuning-list order)."""
    if not labels:
        return True
    if labels[0] == 'e' and labels[-1] == 'E':
        return True
    pitches = [NOTES.index(canonical_pitch_class(l)) for l in labels]
    n = len(pitches)
    if n == 4:
        # Standard bass: G D A E (high-to-low) vs E A D G (low-to-high)
        # Count ascending 4ths (+5 mod 12) vs descending 4ths (-5 == +7 mod 12)
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
    """Infer octave numbers for a list of bare string labels (e.g. ['e', 'B', 'G', 'D', 'A', 'E']).

    Uses monotonic pitch descent (for top-to-bottom tab lines) or ascent (for
    low-to-high lists) anchored at standard guitar/bass registers.
    """
    if not labels:
        return []

    work_labels = list(labels) if top_to_bottom else list(reversed(labels))
    num_strings = len(work_labels)

    # Determine starting semitone anchor for the highest string (work_labels[0])
    top_pc = NOTES.index(canonical_pitch_class(work_labels[0]))
    if instrument_hint == 'bass' or (instrument_hint is None and num_strings <= 5):
        # 4/5-string bass top string is around G2 (semitone 31)
        start_octave = 2 if top_pc >= NOTES.index('C') else 3
    else:
        # 6/7/8-string guitar top string is around D4..F4 (octave 4, or 3 if dropped below B3)
        start_octave = 4 if top_pc <= NOTES.index('G') else 3

    result = []
    prev_semi = start_octave * 12 + top_pc
    result.append(f"{canonical_pitch_class(work_labels[0])}{start_octave}")

    for raw_label in work_labels[1:]:
        pc_name = canonical_pitch_class(raw_label)
        pc = NOTES.index(pc_name)
        # Find the highest pitch with this pitch class that is strictly below prev_semi
        # (unless two adjacent strings are unison, which doesn't happen in standard guitar/bass)
        candidate_oct = prev_semi // 12
        candidate_semi = candidate_oct * 12 + pc
        if candidate_semi >= prev_semi:
            candidate_oct -= 1
            candidate_semi -= 12
        # Guard against huge single-step drops (> 9 semitones) if a string is tuned slightly higher
        # e.g. re-entrant or Nashville tuning is explicit, but for standard/drop tunings intervals are 3..7 semitones
        result.append(f"{pc_name}{candidate_oct}")
        prev_semi = candidate_semi

    if not top_to_bottom:
        result.reverse()
    return result


def detect_source_tuning(tab_lines):
    """Extract source tuning from the first tab section of a file, if present.

    Only labels that include an octave number (e.g. 'E2|---') carry enough
    information to derive an explicit tuning directly. Labels are taken from the
    first group of consecutive tab lines so tunings with repeated notes are
    preserved.
    """
    tuning_pattern = re.compile(r'^([A-Ga-g][#b]?\d+)\s*\|')
    tuning = []

    for line in tab_lines:
        match = tuning_pattern.match(line.strip())
        if match:
            label = match.group(1)
            # Normalize note case (e.g. 'e4' -> 'E4', 'Bb1' -> 'Bb1')
            m = re.match(r'^([A-Ga-g][#b]?)(\d+)$', label)
            note_part, oct_part = m.groups()
            norm_note = note_part[0].upper() + note_part[1:]
            tuning.append(f"{norm_note}{oct_part}")
        elif tuning:
            break  # First section ended

    return tuning or None


TAB_LINE_PATTERN = re.compile(r'^([A-Ga-g][#b]?\d*)\s*\|')


def infer_source_tuning(tab_lines):
    """Infer source tuning from bare tab labels (e.g. 'e|', 'B|', 'G|') and optional preamble text.

    Called as a fallback when detect_source_tuning() returns None so real-world
    internet tabs work without manual file edits or -s flags.
    """
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

    # If some labels had octaves and some didn't, or none had octaves, normalize to bare
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


def parse_tab_file(file_path):
    """Parse tab file and extract tuning and raw lines."""
    with open(file_path) as f:
        lines = [line.rstrip('\n') for line in f]

    source_tuning = detect_source_tuning(lines)
    if source_tuning is None:
        source_tuning = infer_source_tuning(lines)
    return source_tuning, lines


def match_labels_to_strings(section_labels, source_tuning, verbosity=0):
    """Map each tab line in a section to a source string index.

    Labels may include octaves ('E2'), omit them ('E'), or use the common
    lowercase high-string convention ('e'). Supports source_tuning specified in
    either top-to-bottom (high-to-low) or bottom-to-top (low-to-high) order.

    Returns a list of string indices (or None for unmatched lines), one per
    section label.
    """
    bare_tuning = [
        canonical_pitch_class(re.sub(r'\d+', '', note))
        for note in source_tuning
    ]
    tuning_semis = parse_tuning(source_tuning)
    n_tuning = len(source_tuning)
    n_labels = len(section_labels)

    # Determine whether section_labels align better with source_tuning in forward
    # order (pos -> pos) or reversed order (pos -> n_tuning - 1 - pos).
    forward_matches = 0
    reverse_matches = 0
    if n_labels == n_tuning:
        for pos, label in enumerate(section_labels):
            raw_bare = re.sub(r'\d+', '', label)
            try:
                pc = canonical_pitch_class(raw_bare)
            except ValueError:
                continue
            if label.upper() == source_tuning[pos].upper() or pc == bare_tuning[pos]:
                forward_matches += 1
            rev_pos = n_tuning - 1 - pos
            if label.upper() == source_tuning[rev_pos].upper() or pc == bare_tuning[rev_pos]:
                reverse_matches += 1

    use_reverse_order = (n_labels == n_tuning and reverse_matches > forward_matches)
    # Tie-breaker when forward_matches == reverse_matches (e.g. palindromic bare labels
    # or when only outer E strings match): check if 'e' (lowercase) is at pos 0 while
    # source_tuning has higher E at the end (low-to-high).
    if (
        n_labels == n_tuning
        and reverse_matches == forward_matches
        and n_labels >= 2
        and section_labels[0] == 'e'
        and tuning_semis[-1] > tuning_semis[0]
    ):
        use_reverse_order = True

    used = set()
    assignment = []

    for pos, label in enumerate(section_labels):
        raw_bare = re.sub(r'\d+', '', label)
        try:
            bare_label = canonical_pitch_class(raw_bare)
        except ValueError:
            bare_label = raw_bare.upper()

        has_octave = bool(re.search(r'\d', label))
        if has_octave:
            try:
                label_semi = note_to_semitones(label)
            except ValueError:
                label_semi = None
        else:
            label_semi = None

        candidates = [
            i for i, (full, bare, semi) in enumerate(zip(source_tuning, bare_tuning, tuning_semis))
            if i not in used and (
                (label_semi is not None and semi == label_semi)
                or (not has_octave and bare_label == bare)
                or label.upper() == full.upper()
            )
        ]

        if not candidates and n_labels == n_tuning:
            expected_pos = (n_tuning - 1 - pos) if use_reverse_order else pos
            candidates = [expected_pos] if expected_pos not in used else []

        if not candidates:
            if verbosity >= 3:
                print(f"    Warning: could not match label {label!r} to tuning {source_tuning}")
            assignment.append(None)
            continue

        expected_pos = (n_tuning - 1 - pos) if use_reverse_order else pos

        # Disambiguate lowercase 'e' (high E) vs uppercase 'E' (low E) when multiple
        # candidates exist
        if len(candidates) > 1 and not has_octave and raw_bare in ('e', 'E'):
            if raw_bare == 'e' and any(lbl == 'E' for lbl in section_labels):
                idx = max(candidates, key=lambda i: tuning_semis[i])
            elif raw_bare == 'E' and any(lbl == 'e' for lbl in section_labels):
                idx = min(candidates, key=lambda i: tuning_semis[i])
            elif expected_pos in candidates:
                idx = expected_pos
            else:
                idx = candidates[0]
        elif expected_pos in candidates:
            idx = expected_pos
        else:
            idx = candidates[0]

        used.add(idx)
        assignment.append(idx)

    return assignment


def _extract_measures_from_section(contents):
    """Split each tab line's content by '|' into measures and align measure widths across lines.

    Fixes real-world tabs where one string has more dashes in a measure than
    another string in the same measure, while also recording barline columns.

    Returns:
        (aligned_contents, barline_cols)
        where aligned_contents has one string per line with synchronized '|'
        positions, and barline_cols is a sorted set of column indices that hold '|'.
    """
    if not contents:
        return contents, set()

    # Split each line on '|', stripping a single trailing empty segment if all lines end with '|'
    split_lines = [c.split('|') for c in contents]
    if all( len(parts) >= 2 and parts[-1].strip() == '' for parts in split_lines ):
        has_trailing_bar = True
        measures_per_line = [parts[:-1] for parts in split_lines]
    else:
        has_trailing_bar = False
        measures_per_line = split_lines

    measure_counts = {len(m) for m in measures_per_line}
    # If lines don't share the same measure count or only have 1 measure without internal bars,
    # pad to uniform length without rescaling
    if len(measure_counts) != 1:
        max_len = max(len(c) for c in contents)
        return [c.ljust(max_len, '-') for c in contents], set()

    num_measures = next(iter(measure_counts))
    aligned_lines = ['' for _ in contents]
    barline_cols = set()
    current_col = 0

    for m_idx in range(num_measures):
        m_Slices = [line_measures[m_idx] for line_measures in measures_per_line]
        max_m_width = max((len(s) for s in m_Slices), default=0)

        for l_idx, seg in enumerate(m_Slices):
            if len(seg) == max_m_width or len(seg) == 0:
                padded = seg.ljust(max_m_width, '-')
            else:
                # Rescale note positions inside this measure to max_m_width so beats align
                padded = _rescale_measure_segment(seg, max_m_width)
            aligned_lines[l_idx] += padded

        current_col += max_m_width
        if m_idx < num_measures - 1 or has_trailing_bar:
            for l_idx in range(len(contents)):
                aligned_lines[l_idx] += '|'
            barline_cols.add(current_col)
            current_col += 1

    return aligned_lines, barline_cols


def _rescale_measure_segment(seg, target_width):
    """Stretch a shorter measure string to target_width while keeping multi-digit frets intact."""
    if len(seg) >= target_width:
        return seg
    # Extract tokens (multi-digit numbers or non-digit chars) with their start positions
    tokens = []
    i = 0
    n = len(seg)
    while i < n:
        if seg[i].isdigit():
            j = i + 1
            while j < n and seg[j].isdigit():
                j += 1
            tokens.append((i, seg[i:j]))
            i = j
        elif seg[i] != '-':
            tokens.append((i, seg[i]))
            i += 1
        else:
            i += 1

    if not tokens:
        return '-' * target_width

    out = ['-'] * target_width
    scale = (target_width - 1) / max(1, n - 1) if n > 1 else 1.0
    for orig_pos, tok in tokens:
        new_pos = int(round(orig_pos * scale))
        new_pos = min(new_pos, target_width - len(tok))
        for k, ch in enumerate(tok):
            out[new_pos + k] = ch
    return ''.join(out)


def extract_sections_with_metadata(lines, source_tuning, max_fret=24, verbosity=0):
    """Extract note events along with section headers, barline columns, and section widths.

    Returns:
        List of dicts with keys:
          'header': str or None
          'events': {(col, src_string_idx): fret}
          'barlines': set of int
          'width': int
    """
    sections_raw = []
    current_section_lines = []
    last_text_line = None
    current_header = None

    if verbosity >= 3:
        print(f"  Scanning {len(lines)} lines for tab sections...")

    for line in lines:
        if TAB_LINE_PATTERN.match(line.strip()):
            if not current_section_lines:
                current_header = last_text_line
            current_section_lines.append(line)
        else:
            if current_section_lines:
                if verbosity >= 3:
                    print(f"    Section ended with {len(current_section_lines)} lines")
                sections_raw.append((current_header, current_section_lines))
                current_section_lines = []
            if line.strip():
                last_text_line = line.strip()
            else:
                last_text_line = None

    if current_section_lines:
        sections_raw.append((current_header, current_section_lines))

    sections = []

    for header, section_lines in sections_raw:
        section_events = {}
        labels = []
        raw_contents = []
        for line in section_lines:
            note_label, content = line.split('|', 1)
            labels.append(note_label.strip())
            raw_contents.append(content)

        contents, barline_cols = _extract_measures_from_section(raw_contents)
        assignment = match_labels_to_strings(labels, source_tuning, verbosity)
        section_width = max((len(c) for c in contents), default=0)

        for string_idx, content in zip(assignment, contents):
            if string_idx is None:
                continue

            i = 0
            while i < len(content):
                if content[i].isdigit():
                    j = i + 1
                    while j < len(content) and content[j].isdigit():
                        j += 1

                    fret = int(content[i:j])
                    if fret <= max_fret:
                        section_events[(i, string_idx)] = fret
                    elif verbosity >= 2:
                        print(f"    Warning: ignoring fret {fret} > max_fret {max_fret} at column {i}")
                    i = j
                else:
                    i += 1

        if section_events:
            sections.append({
                'header': header,
                'events': section_events,
                'barlines': barline_cols,
                'width': section_width,
            })

    return sections


def extract_note_events(lines, source_tuning, max_fret=24, verbosity=0):
    """Extract note events with their temporal positions (column indices).

    Returns: List of (header, section) tuples, where header is the text line
             preceding the section (e.g. 'Chorus:') or None, and section is a
             dict mapping (column_idx, source_string_idx) -> fret_number.
    """
    detailed = extract_sections_with_metadata(lines, source_tuning, max_fret, verbosity)
    return [(s['header'], s['events']) for s in detailed]


def get_melody_strings(target_tuning, config):
    """Determine which target string indices belong to the melody group.

    Uses the explicit 'melody_strings' config key (list of indices) when
    present. Otherwise splits the tuning in half and picks the half with the
    higher average pitch.
    """
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
    """Return target string indices ordered from top tab line to bottom tab line.

    - Standard guitar/bass tunings are stored low-to-high (E2..E4), so top-to-bottom
      display order reverses them (E4 at top, E2 at bottom).
    - Bundled Chapman Stick tunings with melody strings at indices 0..mid-1 in
      descending pitch order (e.g. D4, A3, E3, B2, F#2, C1, G1, D2, A2, E3) already
      list the top half first; we display melody strings high-to-low at top and
      bass strings high-to-low (or in their natural group order) below.
    """
    num_strings = len(target_tuning)
    if num_strings <= 1:
        return list(range(num_strings))

    if 'display_order' in config:
        return list(config['display_order'])

    target_semi = parse_tuning(target_tuning)
    melody_strings = get_melody_strings(target_tuning, config)

    # Check if this is a split-hand tuning where index 0 is already the highest melody string
    if (
        0 in melody_strings
        and len(melody_strings) < num_strings
        and target_semi[0] > target_semi[min(melody_strings, key=lambda i: target_semi[i])]
    ):
        mel_indices = sorted(melody_strings, key=lambda i: target_semi[i], reverse=True)
        bass_indices = [i for i in range(num_strings) if i not in melody_strings]
        # Display bass strings from highest pitch (closest to center) to lowest pitch (bottom)
        bass_indices_sorted = sorted(bass_indices, key=lambda i: target_semi[i], reverse=True)
        return mel_indices + bass_indices_sorted

    # Default: reverse low-to-high tuning so highest string is on the top line
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


def find_best_target_string(note_pitch, part_type, target_tuning, occupied_strings,
                            config, other_hand_frets=None, prefer_melody_strings=True,
                            prev_fret=None, prev_string=None, chord_frets=None,
                            relax_fret_bounds=False, relax_hand_separation=False,
                            single_part_mode=False):
    """Find the best target string for a note considering playability, voice leading, and chord shapes.

    Args:
        note_pitch: Absolute pitch of the note in semitones
        part_type: 'bass' or 'melody'
        target_tuning: List of target tuning notes
        occupied_strings: Set of string indices already used at this timestamp
        config: Config dict with fret constraints
        other_hand_frets: Dict of {string_idx: fret} for the other hand at this timestamp
        prefer_melody_strings: If True, prefer melody strings for melody parts
        prev_fret: Previous fret played by this hand (for smooth voice leading)
        prev_string: Previous string index played by this hand for the same pitch
        chord_frets: Dict of {string_idx: fret} already placed for THIS hand at this column
        relax_fret_bounds: If True, allow [0, max_fret] with a soft penalty outside [fret_min, fret_max]
        relax_hand_separation: If True, treat hand_separation on non-overlapping string groups as a soft penalty
        single_part_mode: If True and config has no split-hand constraints, don't penalize half the strings

    Returns:
        (target_string_idx, fret) or (None, None) if impossible
    """
    target_semi = parse_tuning(target_tuning)
    num_strings = len(target_semi)
    melody_strings = get_melody_strings(target_tuning, config)

    max_fret = config.get('max_fret', 24)
    hand_separation = config.get('hand_separation', 4)

    if part_type == 'bass':
        fret_min = 0
        fret_max = config.get('bass_max_fret', 12)
        prefer_low_strings = True
    else:
        fret_min = config.get('melody_min_fret', 7)
        fret_max = max_fret
        prefer_low_strings = False

    split_mode = not (single_part_mode and not is_split_hand_config(config))

    candidates = []

    for tgt_idx in range(num_strings):
        if tgt_idx in occupied_strings:
            continue

        tgt_pitch = target_semi[tgt_idx]
        fret = note_pitch - tgt_pitch

        # Hard physical instrument limit
        if fret < 0 or fret > max_fret:
            continue

        # Hand-specific fret range check
        range_penalty = 0.0
        if fret < fret_min or fret > fret_max:
            if not relax_fret_bounds:
                continue
            if fret < fret_min:
                range_penalty = (fret_min - fret) * 2.5 + 8.0
            else:
                range_penalty = (fret - fret_max) * 2.5 + 8.0

        is_melody_string = tgt_idx in melody_strings
        in_preferred_region = (
            (prefer_low_strings and not is_melody_string)
            or (not prefer_low_strings and is_melody_string)
        )

        # Check hand separation constraint
        sep_penalty = 0.0
        if other_hand_frets and hand_separation > 0:
            collision = False
            for other_string, other_fret in other_hand_frets.items():
                fret_gap = abs(fret - other_fret)
                if fret_gap < hand_separation:
                    # If we've crossed into the other hand's string group or strings are adjacent,
                    # always enforce hand separation strictly unless relaxed as a last resort
                    same_region = (other_string in melody_strings) == is_melody_string
                    if not relax_hand_separation or same_region:
                        collision = True
                        break
                    else:
                        sep_penalty = max(sep_penalty, (hand_separation - fret_gap) * 4.0)
            if collision:
                continue

        # Region preference score (lower is better)
        if not split_mode:
            region_penalty = 0.0
        elif prefer_low_strings and not is_melody_string:
            region_penalty = 0.0
        elif prefer_low_strings and is_melody_string:
            region_penalty = 100.0
        elif not prefer_low_strings and is_melody_string:
            region_penalty = 0.0 if prefer_melody_strings else 20.0
        elif not prefer_low_strings and not is_melody_string:
            region_penalty = 100.0 if prefer_melody_strings else 0.0
        else:
            region_penalty = 0.0

        # Ergonomic fret position preference
        fret_center = (fret_min + fret_max) / 2 if split_mode else min(7.0, max_fret / 3)
        fret_penalty = abs(fret - fret_center) * 0.1

        # Voice leading / horizontal continuity cost
        continuity_penalty = 0.0
        if prev_fret is not None:
            continuity_penalty += abs(fret - prev_fret) * 0.35
            if prev_string is not None and tgt_idx == prev_string and fret == prev_fret:
                continuity_penalty -= 1.0  # Strongly favor keeping repeated notes on same string/fret

        # Chord shape compactness cost (keep simultaneous notes in one hand span)
        chord_penalty = 0.0
        if chord_frets:
            frets_in_chord = list(chord_frets.values())
            avg_chord_fret = sum(frets_in_chord) / len(frets_in_chord)
            max_span = max(abs(fret - cf) for cf in frets_in_chord if cf != 0 or fret != 0) if frets_in_chord else 0
            chord_penalty += abs(fret - avg_chord_fret) * 0.8
            if max_span > 4 and fret > 0:
                chord_penalty += (max_span - 4) * 6.0
            min_str_dist = min(abs(tgt_idx - cs) for cs in chord_frets.keys())
            chord_penalty += (min_str_dist - 1) * 0.5

        score = (
            region_penalty
            + range_penalty
            + sep_penalty
            + fret_penalty
            + continuity_penalty
            + chord_penalty
        )
        candidates.append((score, tgt_idx, fret))

    if not candidates:
        return None, None

    candidates.sort()
    _, best_idx, best_fret = candidates[0]
    return best_idx, best_fret


def try_octave_shifts(note_pitch, part_type, target_tuning, occupied_strings,
                     config, other_hand_frets=None, prefer_melody_strings=True,
                     prev_fret=None, prev_string=None, chord_frets=None,
                     single_part_mode=False):
    """Find a playable position, relaxing fret bounds before resorting to octave shifts.

    Priority order:
    1. Exact pitch in preferred string region within [fret_min, fret_max]
    2. Exact pitch in preferred string region relaxing fret bounds / separate-group hand_separation
    3. ±1 octave, then ±2 octaves in preferred string region
    4. Exact pitch in any string region
    5. ±1 octave, then ±2 octaves in any string region

    Returns:
        (target_string_idx, fret) or (None, None) if impossible
    """
    melody_strings = get_melody_strings(target_tuning, config)
    prefer_low = (part_type == 'bass')
    split_mode = not (single_part_mode and not is_split_hand_config(config))

    def in_pref_region(idx):
        if idx is None:
            return False
        if not split_mode:
            return True
        is_mel = idx in melody_strings
        return (prefer_low and not is_mel) or (not prefer_low and is_mel)

    # 1. Exact pitch, strict fret bounds & strict hand separation in preferred region
    result = find_best_target_string(
        note_pitch, part_type, target_tuning, occupied_strings, config,
        other_hand_frets, prefer_melody_strings,
        prev_fret=prev_fret, prev_string=prev_string, chord_frets=chord_frets,
        relax_fret_bounds=False, relax_hand_separation=False,
        single_part_mode=single_part_mode,
    )
    if in_pref_region(result[0]):
        return result

    # 2. Exact pitch in preferred region with relaxed fret bounds / separate-group hand separation
    #    (Playing the true pitch at e.g. fret 5 is much better than shifting an octave!)
    relaxed_exact = find_best_target_string(
        note_pitch, part_type, target_tuning, occupied_strings, config,
        other_hand_frets, prefer_melody_strings,
        prev_fret=prev_fret, prev_string=prev_string, chord_frets=chord_frets,
        relax_fret_bounds=True, relax_hand_separation=True,
        single_part_mode=single_part_mode,
    )
    if in_pref_region(relaxed_exact[0]):
        return relaxed_exact

    # 3. Try ±1 and ±2 octave shifts in preferred region (strict first, then relaxed bounds)
    for relax in (False, True):
        for octave_shift in (12, -12, 24, -24):
            shifted = find_best_target_string(
                note_pitch + octave_shift, part_type, target_tuning, occupied_strings,
                config, other_hand_frets, prefer_melody_strings,
                prev_fret=prev_fret, prev_string=None, chord_frets=chord_frets,
                relax_fret_bounds=relax, relax_hand_separation=relax,
                single_part_mode=single_part_mode,
            )
            if in_pref_region(shifted[0]):
                return shifted

    # 4. Fallback: accept exact pitch in any region
    if result[0] is not None:
        return result
    if relaxed_exact[0] is not None:
        return relaxed_exact

    # 5. Last resort: any octave shift in any region
    for relax in (False, True):
        for octave_shift in (12, -12, 24, -24):
            shifted = find_best_target_string(
                note_pitch + octave_shift, part_type, target_tuning, occupied_strings,
                config, other_hand_frets, prefer_melody_strings,
                prev_fret=prev_fret, prev_string=None, chord_frets=chord_frets,
                relax_fret_bounds=relax, relax_hand_separation=relax,
                single_part_mode=single_part_mode,
            )
            if shifted[0] is not None:
                return shifted

    return None, None


def normalize_section_header(header):
    """Normalize a section header for cross-file matching (e.g. 'Bridge (Repeat 3x):' -> 'bridge')."""
    if not header:
        return None
    s = header.strip().lower().rstrip(':')
    # Remove parenthetical notes like '(repeat 4x)', '(same as intro)'
    s = re.sub(r'\([^)]*\)', '', s)
    # Remove trailing 'section N' or '#N'
    s = re.sub(r'\b(section|part|riff)\s*#?\d+\b', '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    # Map common synonyms
    if s.startswith('intro'):
        return 'intro'
    if s.startswith('outro'):
        return 'outro'
    if 'solo' in s:
        return 'solo'
    if 'chorus' in s and 'pre' not in s:
        return 'chorus'
    if 'pre' in s and 'chorus' in s:
        return 'pre-chorus'
    if 'verse' in s and 'pre' not in s:
        return 'verse'
    if 'bridge' in s:
        return 'bridge'
    return s or None


def align_part_sections(all_parts):
    """Align sections across multiple input parts.

    When all parts have the same number of sections (or no headers), aligns 1-to-1
    by index. When parts have different section counts and carry section headers
    (e.g. Bass has Verse + Chorus + Bridge while Guitar has Chorus + Solo + Bridge),
    uses order-preserving sequence alignment on normalized header keys so Chorus
    pairs with Chorus and Bridge pairs with Bridge instead of drifting out of sync.

    Returns:
        List of aligned slots, where each slot is a list of
        (part_idx, section_dict) tuples present in that slot.
    """
    num_parts = len(all_parts)
    if num_parts == 0:
        return []
    if num_parts == 1:
        return [[(0, sec)] for sec in all_parts[0][2]]

    section_lists = [part[2] for part in all_parts]
    counts = [len(secs) for secs in section_lists]

    # Annotate each section in each part with (effective_group_key, sub_index_within_group)
    annotated = []
    for secs in section_lists:
        part_keys = []
        current_key = None
        sub_counts = defaultdict(int)
        for sec_idx, sec in enumerate(secs):
            raw_header = sec['header'] if isinstance(sec, dict) else sec[0]
            norm = normalize_section_header(raw_header)
            if norm is not None:
                if norm != current_key:
                    current_key = norm
                    sub_counts[current_key] = 0
                else:
                    sub_counts[current_key] += 1
            elif current_key is not None:
                sub_counts[current_key] += 1
            else:
                current_key = f"__sec_{sec_idx}"
                sub_counts[current_key] = 0
            part_keys.append((current_key, sub_counts[current_key]))
        annotated.append(part_keys)

    # Check if simple positional alignment already has matching keys or if counts are equal
    # and no headers contradict each other
    if len(set(counts)) == 1 and annotated[0] == annotated[1]:
        return [
            [(p_idx, section_lists[p_idx][s_idx]) for p_idx in range(num_parts)]
            for s_idx in range(counts[0])
        ]

    # Check if any headers exist at all; if not, fall back to index alignment
    has_any_named_header = any(
        (sec['header'] if isinstance(sec, dict) else sec[0]) is not None
        for secs in section_lists
        for sec in secs
    )
    if not has_any_named_header:
        max_len = max(counts)
        slots = []
        for s_idx in range(max_len):
            slot = [
                (p_idx, section_lists[p_idx][s_idx])
                for p_idx in range(num_parts)
                if s_idx < len(section_lists[p_idx])
            ]
            slots.append(slot)
        return slots

    # Order-preserving LCS alignment starting from part 0 and merging subsequent parts
    # Each slot is a tuple: (slot_key, [(part_idx, sec_dict), ...])
    aligned = [(annotated[0][i], [(0, section_lists[0][i])]) for i in range(len(section_lists[0]))]

    for p_idx in range(1, num_parts):
        other_secs = section_lists[p_idx]
        other_keys = annotated[p_idx]
        m, n = len(aligned), len(other_secs)

        # DP table for LCS on (group_key, sub_index)
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(m - 1, -1, -1):
            for j in range(n - 1, -1, -1):
                if aligned[i][0] == other_keys[j]:
                    dp[i][j] = 1 + dp[i + 1][j + 1]
                else:
                    dp[i][j] = max(dp[i + 1][j], dp[i][j + 1])

        # If LCS found at least one named anchor match, merge along the LCS path;
        # otherwise fall back to positional zip
        if dp[0][0] == 0:
            new_aligned = []
            for k in range(max(m, n)):
                items = []
                key = None
                if k < m:
                    key = aligned[k][0]
                    items.extend(aligned[k][1])
                if k < n:
                    if key is None:
                        key = other_keys[k]
                    items.append((p_idx, other_secs[k]))
                new_aligned.append((key, items))
            aligned = new_aligned
        else:
            new_aligned = []
            i = j = 0
            while i < m and j < n:
                if aligned[i][0] == other_keys[j]:
                    combined = list(aligned[i][1]) + [(p_idx, other_secs[j])]
                    new_aligned.append((aligned[i][0], combined))
                    i += 1
                    j += 1
                elif dp[i + 1][j] >= dp[i][j + 1]:
                    new_aligned.append(aligned[i])
                    i += 1
                else:
                    new_aligned.append((other_keys[j], [(p_idx, other_secs[j])]))
                    j += 1
            while i < m:
                new_aligned.append(aligned[i])
                i += 1
            while j < n:
                new_aligned.append((other_keys[j], [(p_idx, other_secs[j])]))
                j += 1
            aligned = new_aligned

    return [items for _, items in aligned]


def merge_tab_files(file_paths, output_path, config=None, source_tunings_list=None, verbosity=0):
    """Merge multiple tab files into a single combined tab file."""
    target_tuning_list = config.get('target_tuning') if config else None
    if not target_tuning_list:
        print("Error: No target tuning specified in config", file=sys.stderr)
        return 1

    num_target_strings = len(target_tuning_list)
    target_semi = parse_tuning(target_tuning_list)
    single_part_mode = (len(file_paths) == 1)

    all_parts = []  # List of (part_type, source_tuning, sections_metadata) tuples

    for idx, file_path in enumerate(file_paths):
        try:
            detected_tuning, lines = parse_tab_file(file_path)
        except OSError as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

        # Determine source tuning for this file
        if source_tunings_list and idx < len(source_tunings_list):
            source_tuning = source_tunings_list[idx]
        elif detected_tuning:
            source_tuning = detected_tuning
            if verbosity >= 1:
                print(f"Detected source tuning for {file_path.name}: {' '.join(source_tuning)}")
        else:
            print(f"Error: Could not detect source tuning for {file_path}. "
                  f"Specify the tuning with -s (e.g. -s guitar or -s E2,A2,D3,G3,B3,E4).",
                  file=sys.stderr)
            return 1

        try:
            source_semi = parse_tuning(source_tuning)
        except ValueError as e:
            print(f"Error: Invalid source tuning for {file_path}: {e}", file=sys.stderr)
            return 1
        avg_pitch = sum(source_semi) / len(source_semi)
        part_type = 'bass' if avg_pitch < 30 else 'melody'

        if verbosity >= 1:
            print(f"{file_path.name}: {part_type} part (avg pitch {avg_pitch:.1f})")

        sections = extract_sections_with_metadata(
            lines, source_tuning, config.get('max_fret', 24), verbosity
        )
        all_parts.append((part_type, source_tuning, sections))

        if verbosity >= 1:
            print(f"  Found {len(sections)} sections")

    if not any(sections for _, _, sections in all_parts):
        print("Error: No tab sections found in any input file", file=sys.stderr)
        return 1

    section_counts = [len(sections) for _, _, sections in all_parts]
    if len(set(section_counts)) > 1:
        counts = ', '.join(f"{fp.name}: {n}" for fp, n in zip(file_paths, section_counts))
        print(f"Warning: input files have different section counts ({counts}). "
              f"Aligning sections by header name where possible.",
              file=sys.stderr)

    aligned_slots = align_part_sections(all_parts)
    merged_sections = []

    total_notes = 0
    exact_pitch_notes = 0
    octave_shifted_notes = 0
    unmappable_notes = 0

    for section_idx, slot in enumerate(aligned_slots):
        if verbosity >= 2:
            print(f"\nProcessing section {section_idx + 1}/{len(aligned_slots)}")

        section_events = defaultdict(list)  # column -> [(part_type, note_pitch, fret)]
        max_col = 0
        section_header = None
        section_barlines = set()

        for part_idx, sec_meta in slot:
            part_type, source_tuning, _ = all_parts[part_idx]
            header = sec_meta['header']
            section = sec_meta['events']
            barlines = sec_meta.get('barlines', set())
            sec_width = sec_meta.get('width', 0)

            if section_header is None and header:
                section_header = header
            if not section_barlines and barlines:
                section_barlines = set(barlines)
            elif section_barlines and barlines and section_barlines != barlines:
                # Only keep barlines that both parts agree on
                section_barlines &= barlines

            source_semi = parse_tuning(source_tuning)
            max_col = max(max_col, sec_width - 1)

            for (col, src_string_idx), fret in section.items():
                source_pitch = source_semi[src_string_idx]
                note_pitch = source_pitch + fret
                section_events[col].append((part_type, note_pitch, fret))
                max_col = max(max_col, col)

        target_section = defaultdict(dict)  # target_string_idx -> {col: fret}

        # Track voice-leading state per hand across the section
        prev_hand_fret = {'bass': None, 'melody': None}
        prev_pitch_placement = {'bass': {}, 'melody': {}}

        for col in sorted(section_events.keys()):
            events = section_events[col]
            occupied_strings = set()
            bass_frets = {}
            melody_frets = {}

            for pass_type in ['bass', 'melody']:
                pass_events = [e for e in events if e[0] == pass_type]
                # Sort simultaneous chord notes from lowest pitch to highest so chord shapes
                # build consistently from root upward
                pass_events.sort(key=lambda e: e[1])

                for part_type, note_pitch, orig_fret in pass_events:
                    total_notes += 1
                    other_hand_frets = melody_frets if part_type == 'bass' else bass_frets
                    same_hand_frets = bass_frets if part_type == 'bass' else melody_frets

                    prev_str, prev_fr = prev_pitch_placement[part_type].get(
                        note_pitch, (None, prev_hand_fret[part_type])
                    )

                    tgt_idx, new_fret = try_octave_shifts(
                        note_pitch, part_type, target_tuning_list,
                        occupied_strings, config, other_hand_frets,
                        prefer_melody_strings=(part_type == 'melody'),
                        prev_fret=prev_fr if prev_fr is not None else prev_hand_fret[part_type],
                        prev_string=prev_str,
                        chord_frets=same_hand_frets,
                        single_part_mode=single_part_mode,
                    )

                    if tgt_idx is not None:
                        target_section[tgt_idx][col] = new_fret
                        occupied_strings.add(tgt_idx)

                        if part_type == 'bass':
                            bass_frets[tgt_idx] = new_fret
                        else:
                            melody_frets[tgt_idx] = new_fret

                        prev_hand_fret[part_type] = new_fret
                        prev_pitch_placement[part_type][note_pitch] = (tgt_idx, new_fret)

                        placed_pitch = target_semi[tgt_idx] + new_fret
                        if placed_pitch == note_pitch:
                            exact_pitch_notes += 1
                        else:
                            octave_shifted_notes += 1

                        if verbosity >= 2:
                            shift_semis = placed_pitch - note_pitch
                            shift_str = f" (shifted {shift_semis:+d} st)" if shift_semis else ""
                            print(f"  Col {col}: {part_type} note (pitch {note_pitch}) -> "
                                  f"string {tgt_idx} fret {new_fret}{shift_str}")
                    else:
                        unmappable_notes += 1
                        if verbosity >= 2:
                            print(f"  Col {col}: {part_type} note (pitch {note_pitch}) -> UNMAPPABLE (X)")
                        for fallback_idx in range(num_target_strings):
                            if fallback_idx not in occupied_strings:
                                target_section[fallback_idx][col] = 'X'
                                occupied_strings.add(fallback_idx)
                                break

        merged_sections.append((section_header, target_section, max_col, section_barlines))

    # Render output lines
    merged_lines = []
    target_displays = [re.sub(r'\d+', '', note) for note in target_tuning_list]
    max_label_width = max(len(d) for d in target_displays)
    display_order = get_display_string_order(target_tuning_list, config)

    for section_idx, (section_header, target_section, max_col, barlines) in enumerate(merged_sections):
        if section_idx > 0:
            merged_lines.append("")

        if section_header:
            merged_lines.append(section_header)

        # Determine width needed at each column position
        col_widths = {}
        has_note_at_col = {}
        for col in range(max_col + 1):
            max_width = 1
            any_note = False
            for tgt_idx in range(num_target_strings):
                if tgt_idx in target_section and col in target_section[tgt_idx]:
                    any_note = True
                    fret = target_section[tgt_idx][col]
                    width = 1 if fret == 'X' else len(str(fret))
                    max_width = max(max_width, width)
            col_widths[col] = max_width
            has_note_at_col[col] = any_note

        # Absorb trailing empty spacer column after a 2-digit fret so multi-digit
        # frets don't stretch the tab width compared to the input
        skip_cols = set()
        for col in range(max_col):
            if (
                col not in skip_cols
                and col_widths[col] == 2
                and not has_note_at_col.get(col + 1, False)
                and (col + 1) not in barlines
            ):
                skip_cols.add(col + 1)

        for tgt_idx in display_order:
            label = target_displays[tgt_idx].ljust(max_label_width)

            content = []
            for col in range(max_col + 1):
                if col in skip_cols:
                    continue
                col_width = col_widths[col]

                if tgt_idx in target_section and col in target_section[tgt_idx]:
                    fret = target_section[tgt_idx][col]
                    fret_str = 'X' if fret == 'X' else str(fret)
                    content.append(fret_str.ljust(col_width, '-'))
                elif col in barlines:
                    content.append('|')
                else:
                    content.append('-' * col_width)

            merged_lines.append(f"{label}|{''.join(content)}")

    output_text = '\n'.join(merged_lines) + '\n'

    if output_path is None:
        sys.stdout.write(output_text)
    else:
        try:
            with open(output_path, 'w') as f:
                f.write(output_text)
        except OSError as e:
            print(f"Error writing output file: {e}", file=sys.stderr)
            return 1
        print(f"\nMerged {len(file_paths)} files into {output_path}")
        print(f"Combined {len(merged_sections)} section(s)")
        if verbosity >= 1 and total_notes > 0:
            print(
                f"Notes mapped: {exact_pitch_notes}/{total_notes} exact pitch, "
                f"{octave_shifted_notes} octave-shifted, {unmappable_notes} unplayable"
            )

    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Convert and merge guitar/bass tabs into any target tuning or multi-string instrument tab'
    )

    parser.add_argument('inputs', type=Path, nargs='*',
                        help='Input tab file(s) to convert or merge')
    parser.add_argument('-o', '--output', type=Path,
                        help='Output tab file (default: print to stdout)')
    parser.add_argument('-c', '--config', '-t', '--target-tuning', dest='config',
                        help='Target tuning: a JSON config path, a bundled tuning name '
                             '(e.g. "stick/10string/classic"), a shorthand alias '
                             '(e.g. "stick", "dropd", "dadgad", "guitar", "bass"), or '
                             'comma-separated notes (e.g. "D2,A2,D3,G3,A3,D4"). '
                             'See --list-tunings for bundled names')
    parser.add_argument('-s', '--source-tuning', action='append',
                        help='Source tuning for input file as comma-separated notes (e.g., "E1,A1,D2,G2"), '
                             'bundled tuning name, or alias (e.g., "bass", "guitar", "dropd"). '
                             'If omitted, auto-detected/inferred from the tab file')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity (-v for summary, -vv for note mapping, -vvv for parser debug)')
    parser.add_argument('--list-tunings', action='store_true',
                        help='List bundled tuning names and exit')

    args = parser.parse_args()

    if args.list_tunings:
        for name in list_bundled_tunings():
            print(name)
        return 0

    if not args.inputs:
        parser.error('at least one input tab file is required')
    if not args.config:
        parser.error('-c/--config (or -t/--target-tuning) is required')

    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    source_tunings_list = None
    if args.source_tuning:
        source_tunings_list = []
        for tuning_str in args.source_tuning:
            try:
                tuning = parse_source_tuning_arg(tuning_str)
            except ValueError as e:
                print(f"Error parsing source tuning {tuning_str!r}: {e}", file=sys.stderr)
                return 1
            source_tunings_list.append(tuning)

    return merge_tab_files(args.inputs, args.output, config, source_tunings_list, args.verbose)


if __name__ == '__main__':
    sys.exit(main())
