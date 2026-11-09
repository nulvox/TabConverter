"""Parsers for ASCII tablature and structured multi-track formats (MIDI, MusicXML, Guitar Pro)."""

import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .music import (
    NOTES,
    TAB_LINE_PATTERN,
    canonical_pitch_class,
    detect_source_tuning,
    infer_source_tuning,
    midi_to_note,
    midi_to_semitones,
    note_to_semitones,
    parse_tuning,
    semitones_to_note,
)


STRUCTURED_EXTENSIONS = {
    '.mid', '.midi',
    '.musicxml', '.xml', '.mxl',
    '.gp', '.gp3', '.gp4', '.gp5', '.gpx',
}


@dataclass
class TechniqueInfo:
    """Articulation and playing-technique metadata attached to a note event."""
    bend_semitones: int = 0
    release_offset: int | None = None
    vibrato: str = ''
    connector_before: str = ''  # 'h', 'p', '/', '\'
    linked_prev_col: int | None = None
    is_dead_note: bool = False

    def format_fret(self, fret):
        """Format a placed fret number with its articulation suffixes."""
        if self.is_dead_note or fret == 'x':
            return 'x'
        if fret == 'X':
            return 'X'
        base = str(fret)
        if self.bend_semitones > 0:
            target = fret + self.bend_semitones
            s = f"{base}b{target}"
            if self.release_offset is not None:
                s += f"r{fret + self.release_offset}"
        else:
            s = base
        if self.vibrato:
            s += self.vibrato
        return s


@dataclass
class TrackSummary:
    """Summary of a track inside a structured multi-track file (MIDI, MusicXML, Guitar Pro)."""
    index: int  # 1-based index
    name: str
    part_type: str  # 'bass', 'melody', or 'percussion'
    source_tuning: list[str]
    note_count: int = 0


def is_structured_file(file_path):
    """Return True if file_path has a structured score/tab extension (.mid, .musicxml, .gp*, etc.)."""
    return Path(file_path).suffix.lower() in STRUCTURED_EXTENSIONS


def parse_tab_file(file_path):
    """Parse an ASCII tab file and extract tuning and raw lines."""
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
    """
    bare_tuning = [
        canonical_pitch_class(re.sub(r'\d+', '', note))
        for note in source_tuning
    ]
    tuning_semis = parse_tuning(source_tuning)
    n_tuning = len(source_tuning)
    n_labels = len(section_labels)

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
    """Split each tab line's content by '|' into measures and align measure widths across lines."""
    if not contents:
        return contents, set()

    split_lines = [c.split('|') for c in contents]
    if all(len(parts) >= 2 and parts[-1].strip() == '' for parts in split_lines):
        has_trailing_bar = True
        measures_per_line = [parts[:-1] for parts in split_lines]
    else:
        has_trailing_bar = False
        measures_per_line = split_lines

    measure_counts = {len(m) for m in measures_per_line}
    if len(measure_counts) != 1:
        max_len = max(len(c) for c in contents)
        return [c.ljust(max_len, '-') for c in contents], set()

    num_measures = next(iter(measure_counts))
    aligned_lines = ['' for _ in contents]
    barline_cols = set()
    current_col = 0

    for m_idx in range(num_measures):
        m_slices = [line_measures[m_idx] for line_measures in measures_per_line]
        max_m_width = max((len(s) for s in m_slices), default=0)

        for l_idx, seg in enumerate(m_slices):
            if len(seg) == max_m_width or len(seg) == 0:
                padded = seg.ljust(max_m_width, '-')
            else:
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
    """Stretch a shorter measure string to target_width while keeping multi-digit/technique tokens intact."""
    if len(seg) >= target_width:
        return seg
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


_BEND_RE = re.compile(r'^(\d+)b\(?(\d+)\)?(?:r\(?(\d+)\)?)?(~*)')


def _scan_tab_line_events(content, string_idx, max_fret, section_events, techniques, dead_notes, verbosity=0):
    """Scan a single string's tab content for notes, bends, legato/slides, vibrato, and dead notes."""
    i = 0
    n = len(content)
    pending_connector = ''
    prev_note_col = None

    while i < n:
        ch = content[i]
        if ch.isdigit():
            # Check for bend expression first: e.g. 7b9, 7b(9), 7b9r7, 7b9~
            bend_match = _BEND_RE.match(content[i:])
            if bend_match:
                base_str, target_str, release_str, vib_str = bend_match.groups()
                base_fret = int(base_str)
                target_fret = int(target_str)
                if base_fret <= max_fret and target_fret > base_fret:
                    section_events[(i, string_idx)] = base_fret
                    rel_offset = (int(release_str) - base_fret) if release_str is not None else None
                    techniques[(i, string_idx)] = TechniqueInfo(
                        bend_semitones=target_fret - base_fret,
                        release_offset=rel_offset,
                        vibrato=vib_str or '',
                        connector_before=pending_connector,
                        linked_prev_col=prev_note_col if pending_connector else None,
                    )
                    prev_note_col = i
                pending_connector = ''
                i += bend_match.end()
                continue

            # Standard single or multi-digit fret
            j = i + 1
            while j < n and content[j].isdigit():
                j += 1
            fret = int(content[i:j])

            # Check for trailing vibrato '~'
            vib_end = j
            while vib_end < n and content[vib_end] == '~':
                vib_end += 1
            vib_str = content[j:vib_end]

            if fret <= max_fret:
                section_events[(i, string_idx)] = fret
                if vib_str or pending_connector:
                    techniques[(i, string_idx)] = TechniqueInfo(
                        vibrato=vib_str,
                        connector_before=pending_connector,
                        linked_prev_col=prev_note_col if pending_connector else None,
                    )
                prev_note_col = i
            elif verbosity >= 2:
                print(f"    Warning: ignoring fret {fret} > max_fret {max_fret} at column {i}")

            pending_connector = ''
            i = vib_end
        elif ch in ('h', 'p', '/', '\\') and prev_note_col is not None and i + 1 < n and content[i + 1].isdigit():
            pending_connector = ch
            i += 1
        elif ch in ('x', 'X'):
            # Dead/muted note when surrounded by dashes, pipes, or line boundaries
            prev_ok = (i == 0 or content[i - 1] in '-| ')
            next_ok = (i + 1 == n or content[i + 1] in '-| ')
            if prev_ok and next_ok:
                dead_notes[(i, string_idx)] = 'x'
            pending_connector = ''
            prev_note_col = None
            i += 1
        else:
            if ch in ('-', '|', ' '):
                pending_connector = ''
                if ch == '|':
                    prev_note_col = None
            i += 1


def extract_sections_with_metadata(lines, source_tuning, max_fret=24, verbosity=0):
    """Extract note events along with section headers, barlines, widths, and playing techniques.

    Returns:
        List of dicts with keys:
          'header': str or None
          'events': {(col, src_string_idx): fret}
          'techniques': {(col, src_string_idx): TechniqueInfo}
          'dead_notes': {(col, src_string_idx): 'x'}
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
        techniques = {}
        dead_notes = {}
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
            _scan_tab_line_events(
                content, string_idx, max_fret,
                section_events, techniques, dead_notes, verbosity
            )

        if section_events or dead_notes:
            sections.append({
                'header': header,
                'events': section_events,
                'techniques': techniques,
                'dead_notes': dead_notes,
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
    return [(s['header'], s['events']) for s in detailed if s['events']]


# ============================================================================
# Structured Multi-Track Parsers (MIDI, MusicXML, Guitar Pro)
# ============================================================================

def _assign_notes_to_virtual_tuning(pitches_at_col, tuning):
    """Map a list of absolute semitone pitches at a timestamp to (string_idx, fret) on a virtual tuning."""
    tuning_semi = parse_tuning(tuning)
    used = set()
    placements = []
    for pitch in sorted(pitches_at_col):
        best = None
        for s_idx, s_semi in enumerate(tuning_semi):
            if s_idx in used:
                continue
            fret = pitch - s_semi
            if 0 <= fret <= 24:
                score = abs(fret - 5)
                if best is None or score < best[0]:
                    best = (score, s_idx, fret)
        if best is None:
            # Clamp or shift into range of nearest string
            for s_idx, s_semi in enumerate(tuning_semi):
                if s_idx in used:
                    continue
                fret = pitch - s_semi
                while fret < 0:
                    fret += 12
                while fret > 24:
                    fret -= 12
                if 0 <= fret <= 24:
                    best = (100, s_idx, fret)
                    break
        if best is not None:
            _, s_idx, fret = best
            used.add(s_idx)
            placements.append((s_idx, fret))
    return placements


def _read_vlq(data, pos):
    """Read a standard MIDI variable-length quantity (VLQ) starting at pos."""
    value = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return value, pos


def _parse_midi_file(file_path):
    """Parse a Standard MIDI File (.mid/.midi) into track summaries and quantized section metadata."""
    data = Path(file_path).read_bytes()
    if len(data) < 14 or data[:4] != b'MThd':
        raise ValueError(f"{file_path} is not a valid Standard MIDI file (missing MThd header)")

    hdr_len, fmt, num_tracks, division = struct.unpack('>IHhH', data[4:14])
    if division & 0x8000:
        ticks_per_beat = 480
    else:
        ticks_per_beat = max(1, division)

    pos = 8 + hdr_len
    raw_tracks = []
    time_sig_num = 4
    time_sig_den = 4

    for t_idx in range(num_tracks):
        if pos + 8 > len(data):
            break
        chunk_id = data[pos:pos + 4]
        chunk_len = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        chunk_end = pos + 8 + chunk_len
        pos = pos + 8
        if chunk_id != b'MTrk':
            pos = chunk_end
            continue

        tick = 0
        running_status = 0
        track_name = None
        programs = set()
        channels = set()
        notes = []  # (tick, channel, midi_note)

        while pos < chunk_end:
            delta, pos = _read_vlq(data, pos)
            tick += delta
            if pos >= chunk_end:
                break
            status = data[pos]
            if status >= 0x80:
                pos += 1
                if status < 0xF0:
                    running_status = status
            else:
                status = running_status

            if status == 0xFF:  # Meta event
                if pos >= chunk_end:
                    break
                meta_type = data[pos]
                pos += 1
                meta_len, pos = _read_vlq(data, pos)
                meta_bytes = data[pos:pos + meta_len]
                pos += meta_len
                if meta_type == 0x03 and not track_name:
                    track_name = meta_bytes.decode('utf-8', errors='replace').strip()
                elif meta_type == 0x58 and len(meta_bytes) >= 2:
                    time_sig_num = meta_bytes[0]
                    time_sig_den = 2 ** meta_bytes[1]
            elif status in (0xF0, 0xF7):  # SysEx
                sysex_len, pos = _read_vlq(data, pos)
                pos += sysex_len
            else:
                msg_type = status & 0xF0
                ch = status & 0x0F
                if msg_type in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    p1 = data[pos] if pos < chunk_end else 0
                    p2 = data[pos + 1] if pos + 1 < chunk_end else 0
                    pos += 2
                    if msg_type == 0x90 and p2 > 0:
                        channels.add(ch)
                        notes.append((tick, ch, p1))
                elif msg_type in (0xC0, 0xD0):
                    p1 = data[pos] if pos < chunk_end else 0
                    pos += 1
                    if msg_type == 0xC0:
                        programs.add(p1)
                else:
                    break

        pos = chunk_end
        if notes:
            raw_tracks.append({
                'name': track_name or f"Track {len(raw_tracks) + 1}",
                'programs': programs,
                'channels': channels,
                'notes': notes,
            })

    # If Format 0 (single MTrk with multiple MIDI channels), split by channel
    if len(raw_tracks) == 1 and len(raw_tracks[0]['channels']) > 1:
        single = raw_tracks[0]
        by_ch = defaultdict(list)
        for tick, ch, note in single['notes']:
            by_ch[ch].append((tick, ch, note))
        raw_tracks = [
            {
                'name': f"{single['name']} (Ch {ch + 1})",
                'programs': single['programs'],
                'channels': {ch},
                'notes': ch_notes,
            }
            for ch, ch_notes in sorted(by_ch.items())
        ]

    ticks_per_measure = int(ticks_per_beat * time_sig_num * (4 / max(1, time_sig_den)))
    ticks_per_measure = max(ticks_per_beat, ticks_per_measure)
    cols_per_measure = 16  # 16th-note grid per measure

    max_tick = max((n[0] for tr in raw_tracks for n in tr['notes']), default=0)
    num_measures = max(1, (max_tick // ticks_per_measure) + 1)
    total_width = num_measures * (cols_per_measure + 1)
    barlines = {m * (cols_per_measure + 1) + cols_per_measure for m in range(num_measures)}

    parsed_tracks = []
    for idx, tr in enumerate(raw_tracks, start=1):
        is_percussion = (tr['channels'] == {9}) or ('drum' in tr['name'].lower()) or ('perc' in tr['name'].lower())
        semis = [midi_to_semitones(n[2]) for n in tr['notes']]
        avg_semi = sum(semis) / len(semis) if semis else 40.0
        is_bass_prog = any(32 <= p <= 39 for p in tr['programs'])
        is_bass_name = 'bass' in tr['name'].lower()

        if is_percussion:
            part_type = 'percussion'
            tuning = ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']
        elif is_bass_prog or is_bass_name or avg_semi < 32:
            part_type = 'bass'
            min_semi = min(semis) if semis else 16
            tuning = ['B0', 'E1', 'A1', 'D2', 'G2'] if min_semi < 16 else ['E1', 'A1', 'D2', 'G2']
        else:
            part_type = 'melody'
            tuning = ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']

        col_to_pitches = defaultdict(list)
        for tick, _ch, midi_note in tr['notes']:
            m_idx = tick // ticks_per_measure
            tick_in_m = tick % ticks_per_measure
            col_in_m = int(round((tick_in_m / ticks_per_measure) * cols_per_measure))
            if col_in_m >= cols_per_measure:
                m_idx += 1
                col_in_m = 0
            col = m_idx * (cols_per_measure + 1) + col_in_m
            col_to_pitches[col].append(midi_to_semitones(midi_note))

        events = {}
        for col, pitches in col_to_pitches.items():
            for s_idx, fret in _assign_notes_to_virtual_tuning(pitches, tuning):
                events[(col, s_idx)] = fret

        summary = TrackSummary(
            index=idx,
            name=tr['name'],
            part_type=part_type,
            source_tuning=tuning,
            note_count=len(tr['notes']),
        )
        section = {
            'header': None,
            'events': events,
            'techniques': {},
            'dead_notes': {},
            'barlines': set(barlines),
            'width': total_width,
        }
        parsed_tracks.append((summary, [section]))

    return parsed_tracks


def _strip_xml_ns(elem):
    """Strip XML namespaces in-place for cleaner ElementTree traversal."""
    for el in elem.iter():
        if '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]
    return elem


def _load_musicxml_root(file_path):
    """Load root ElementTree element from a .musicxml, .xml, or compressed .mxl file."""
    path = Path(file_path)
    if path.suffix.lower() == '.mxl' or zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, 'r') as zf:
            xml_target = None
            if 'META-INF/container.xml' in zf.namelist():
                container = _strip_xml_ns(ET.fromstring(zf.read('META-INF/container.xml')))
                rf = container.find('.//rootfile')
                if rf is not None and rf.get('full-path'):
                    xml_target = rf.get('full-path')
            if not xml_target:
                candidates = [
                    n for n in zf.namelist()
                    if n.lower().endswith(('.musicxml', '.xml')) and not n.startswith('META-INF')
                ]
                if not candidates:
                    raise ValueError(f"No MusicXML score found inside archive {file_path}")
                xml_target = candidates[0]
            root = ET.fromstring(zf.read(xml_target))
    else:
        root = ET.parse(path).getroot()
    return _strip_xml_ns(root)


def _parse_musicxml_file(file_path):
    """Parse a MusicXML (.musicxml, .xml, .mxl) score into track summaries and section metadata."""
    root = _load_musicxml_root(file_path)

    part_names = {}
    for sp in root.findall('.//part-list/score-part'):
        pid = sp.get('id', '')
        pname_el = sp.find('part-name')
        pname = (pname_el.text or '').strip() if pname_el is not None else pid
        part_names[pid] = pname or pid

    parts_xml = root.findall('./part')
    cols_per_measure = 16

    # Determine max measure count across all parts
    num_measures = max((len(p.findall('./measure')) for p in parts_xml), default=1)
    num_measures = max(1, num_measures)
    total_width = num_measures * (cols_per_measure + 1)
    barlines = {m * (cols_per_measure + 1) + cols_per_measure for m in range(num_measures)}

    parsed_tracks = []

    for idx, part_el in enumerate(parts_xml, start=1):
        pid = part_el.get('id', f'P{idx}')
        name = part_names.get(pid, f'Part {idx}')

        # Extract staff tuning if declared in <staff-details>
        tuning_lines = {}
        for st in part_el.findall('.//staff-details/staff-tuning'):
            line_num = int(st.get('line', '1'))
            step = st.findtext('tuning-step', 'E').strip()
            alter = int(float(st.findtext('tuning-alter', '0') or 0))
            octave = int(st.findtext('tuning-octave', '2') or 2)
            base_semi = NOTES.index(canonical_pitch_class(step)) + alter + octave * 12
            tuning_lines[line_num] = semitones_to_note(base_semi)

        explicit_tuning = [tuning_lines[k] for k in sorted(tuning_lines)] if tuning_lines else None

        divisions = 1
        beats_per_bar = 4
        beat_type = 4
        has_unpitched = False
        raw_notes = []  # (col, pitch_semi, explicit_str_idx, explicit_fret, tech)

        for m_idx, measure_el in enumerate(part_el.findall('./measure')):
            attr_el = measure_el.find('./attributes')
            if attr_el is not None:
                div_text = attr_el.findtext('divisions')
                if div_text:
                    divisions = max(1, int(float(div_text)))
                time_el = attr_el.find('time')
                if time_el is not None:
                    b_text = time_el.findtext('beats')
                    bt_text = time_el.findtext('beat-type')
                    if b_text and bt_text:
                        beats_per_bar = int(b_text)
                        beat_type = int(bt_text)

            measure_duration = max(1.0, divisions * beats_per_bar * (4.0 / max(1, beat_type)))
            cursor = 0.0
            last_note_start = 0.0

            for child in measure_el:
                if child.tag == 'backup':
                    dur = float(child.findtext('duration', '0') or 0)
                    cursor = max(0.0, cursor - dur)
                elif child.tag == 'forward':
                    dur = float(child.findtext('duration', '0') or 0)
                    cursor += dur
                elif child.tag == 'note':
                    is_chord = child.find('chord') is not None
                    is_rest = child.find('rest') is not None
                    dur = float(child.findtext('duration', '0') or 0)
                    note_pos = last_note_start if is_chord else cursor

                    if not is_chord:
                        last_note_start = cursor
                        cursor += dur

                    if is_rest:
                        continue
                    if child.find('unpitched') is not None:
                        has_unpitched = True
                        continue

                    # Ignore tied-continuation notes (<tie type="stop"/> without start)
                    tie_types = {t.get('type') for t in child.findall('tie')}
                    if 'stop' in tie_types and 'start' not in tie_types:
                        continue

                    pitch_el = child.find('pitch')
                    if pitch_el is None:
                        continue
                    step = pitch_el.findtext('step', 'C').strip()
                    alter = int(float(pitch_el.findtext('alter', '0') or 0))
                    octave = int(pitch_el.findtext('octave', '4') or 4)
                    pitch_semi = NOTES.index(canonical_pitch_class(step)) + alter + octave * 12

                    # Extract technical string/fret and articulations if present
                    tech_el = child.find('./notations/technical')
                    exp_string = None
                    exp_fret = None
                    bend_semis = 0
                    conn = ''
                    if tech_el is not None:
                        s_text = tech_el.findtext('string')
                        f_text = tech_el.findtext('fret')
                        if s_text and f_text:
                            # MusicXML string 1 is highest string
                            exp_string = int(s_text)
                            exp_fret = int(f_text)
                        bend_el = tech_el.find('bend')
                        if bend_el is not None:
                            b_alter = float(bend_el.findtext('bend-alter', '0') or 0)
                            bend_semis = max(0, int(round(b_alter)))
                        for tag_name, sym in (('hammer-on', 'h'), ('pull-off', 'p'), ('slide', '/')):
                            for t_node in tech_el.findall(tag_name):
                                if t_node.get('type') == 'stop':
                                    conn = sym

                    col_in_m = int(round((note_pos / measure_duration) * cols_per_measure))
                    col_in_m = max(0, min(cols_per_measure - 1, col_in_m))
                    col = m_idx * (cols_per_measure + 1) + col_in_m
                    raw_notes.append((col, pitch_semi, exp_string, exp_fret, bend_semis, conn))

        all_semis = [n[1] for n in raw_notes]
        avg_semi = sum(all_semis) / len(all_semis) if all_semis else 40.0
        is_perc = has_unpitched and not raw_notes or ('drum' in name.lower() or 'perc' in name.lower())

        if is_perc:
            part_type = 'percussion'
            tuning = explicit_tuning or ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']
        elif 'bass' in name.lower() or (explicit_tuning and len(explicit_tuning) <= 5) or avg_semi < 30:
            part_type = 'bass'
            tuning = explicit_tuning or ['E1', 'A1', 'D2', 'G2']
        else:
            part_type = 'melody'
            tuning = explicit_tuning or ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']

        events = {}
        techniques = {}
        col_unassigned = defaultdict(list)
        n_strings = len(tuning)

        for col, pitch_semi, exp_string, exp_fret, bend_semis, conn in raw_notes:
            if exp_string is not None and exp_fret is not None and 1 <= exp_string <= n_strings:
                # MusicXML string 1 is the highest-pitched string; our tuning is low-to-high
                s_idx = n_strings - exp_string
                events[(col, s_idx)] = exp_fret
                if bend_semis > 0 or conn:
                    techniques[(col, s_idx)] = TechniqueInfo(
                        bend_semitones=bend_semis,
                        connector_before=conn,
                    )
            else:
                col_unassigned[col].append((pitch_semi, bend_semis, conn))

        for col, items in col_unassigned.items():
            pitches = [it[0] for it in items]
            for (s_idx, fret), (_p, bend_semis, conn) in zip(
                _assign_notes_to_virtual_tuning(pitches, tuning), sorted(items, key=lambda x: x[0])
            ):
                events[(col, s_idx)] = fret
                if bend_semis > 0 or conn:
                    techniques[(col, s_idx)] = TechniqueInfo(
                        bend_semitones=bend_semis,
                        connector_before=conn,
                    )

        summary = TrackSummary(
            index=idx,
            name=name,
            part_type=part_type,
            source_tuning=tuning,
            note_count=len(raw_notes),
        )
        section = {
            'header': None,
            'events': events,
            'techniques': techniques,
            'dead_notes': {},
            'barlines': set(barlines),
            'width': total_width,
        }
        parsed_tracks.append((summary, [section]))

    return parsed_tracks


def _parse_gp_zip_file(file_path):
    """Parse a Guitar Pro 7/8 (.gp) zip container (Content/score.gpif XML)."""
    with zipfile.ZipFile(file_path, 'r') as zf:
        gpif_name = next((n for n in zf.namelist() if n.endswith('score.gpif')), None)
        if not gpif_name:
            raise ValueError(f"No Content/score.gpif found in Guitar Pro archive {file_path}")
        root = _strip_xml_ns(ET.fromstring(zf.read(gpif_name)))

    # Map Notes by id
    notes_by_id = {}
    for note_el in root.findall('.//Notes/Note'):
        nid = note_el.get('id')
        fret_val = None
        str_val = None
        is_tie_dest = False
        for tie_el in note_el.findall('./Tie'):
            if tie_el.get('destination', '').lower() == 'true':
                is_tie_dest = True
        for prop in note_el.findall('./Properties/Property'):
            pname = prop.get('name')
            if pname == 'Fret':
                fret_val = int(prop.findtext('Fret', '0') or 0)
            elif pname == 'String':
                str_val = int(prop.findtext('String', '0') or 0)
        if nid is not None and fret_val is not None and str_val is not None and not is_tie_dest:
            notes_by_id[nid] = (str_val, fret_val)

    # Map Rhythms by id -> duration in quarter-note beats
    note_val_map = {'Whole': 4.0, 'Half': 2.0, 'Quarter': 1.0, 'Eighth': 0.5, '16th': 0.25, '32nd': 0.125, '64th': 0.0625}
    rhythms_by_id = {}
    for r_el in root.findall('.//Rhythms/Rhythm'):
        rid = r_el.get('id')
        nv = r_el.findtext('NoteValue', 'Quarter')
        dur = note_val_map.get(nv, 1.0)
        dot_el = r_el.find('AugmentationDot')
        if dot_el is not None:
            cnt = int(dot_el.get('count', '1'))
            if cnt == 1:
                dur *= 1.5
            elif cnt >= 2:
                dur *= 1.75
        tuplet_el = r_el.find('PrimaryTuplet')
        if tuplet_el is not None:
            num = int(tuplet_el.get('num', '1'))
            den = int(tuplet_el.get('den', '1'))
            if num > 0:
                dur *= (den / num)
        rhythms_by_id[rid] = dur

    # Map Beats by id -> (duration, [note_ids])
    beats_by_id = {}
    for b_el in root.findall('.//Beats/Beat'):
        bid = b_el.get('id')
        r_ref = b_el.find('Rhythm')
        rid = r_ref.get('ref') if r_ref is not None else None
        dur = rhythms_by_id.get(rid, 1.0)
        n_text = (b_el.findtext('Notes') or '').strip()
        nids = n_text.split() if n_text else []
        beats_by_id[bid] = (dur, nids)

    # Map Voices by id -> [beat_ids]
    voices_by_id = {}
    for v_el in root.findall('.//Voices/Voice'):
        vid = v_el.get('id')
        b_text = (v_el.findtext('Beats') or '').strip()
        voices_by_id[vid] = b_text.split() if b_text else []

    # Map Bars by id -> [voice_ids]
    bars_by_id = {}
    for bar_el in root.findall('.//Bars/Bar'):
        bar_id = bar_el.get('id')
        v_text = (bar_el.findtext('Voices') or '').strip()
        bars_by_id[bar_id] = [v for v in v_text.split() if v != '-1']

    master_bars = root.findall('.//MasterBars/MasterBar')
    cols_per_measure = 16
    num_measures = max(1, len(master_bars))
    total_width = num_measures * (cols_per_measure + 1)
    barlines = {m * (cols_per_measure + 1) + cols_per_measure for m in range(num_measures)}

    tracks_el = root.findall('.//Tracks/Track')
    parsed_tracks = []

    for t_pos, tr_el in enumerate(tracks_el):
        name = (tr_el.findtext('Name') or f'Track {t_pos + 1}').strip()
        pitches_text = ''
        for prop in tr_el.findall('.//Properties/Property'):
            if prop.get('name') == 'Tuning':
                pitches_text = (prop.findtext('Pitches') or '').strip()
                break
        if pitches_text:
            midi_pitches = [int(p) for p in pitches_text.split()]
            tuning = [midi_to_note(mp) for mp in midi_pitches]
        else:
            tuning = ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']

        is_perc = ('drum' in name.lower() or 'perc' in name.lower())
        semis = parse_tuning(tuning) if tuning else [40]
        avg_semi = sum(semis) / len(semis)
        if is_perc:
            part_type = 'percussion'
        elif 'bass' in name.lower() or avg_semi < 30:
            part_type = 'bass'
        else:
            part_type = 'melody'

        events = {}
        note_count = 0

        for m_idx, mb_el in enumerate(master_bars):
            bars_text = (mb_el.findtext('Bars') or '').strip().split()
            if t_pos >= len(bars_text):
                continue
            bar_id = bars_text[t_pos]
            for vid in bars_by_id.get(bar_id, [])[:1]:
                cursor = 0.0
                for bid in voices_by_id.get(vid, []):
                    dur, nids = beats_by_id.get(bid, (1.0, []))
                    col_in_m = int(round((cursor / 4.0) * cols_per_measure))
                    col_in_m = max(0, min(cols_per_measure - 1, col_in_m))
                    col = m_idx * (cols_per_measure + 1) + col_in_m
                    for nid in nids:
                        if nid in notes_by_id:
                            str_idx, fret_val = notes_by_id[nid]
                            if 0 <= str_idx < len(tuning):
                                events[(col, str_idx)] = fret_val
                                note_count += 1
                    cursor += dur

        summary = TrackSummary(
            index=t_pos + 1,
            name=name,
            part_type=part_type,
            source_tuning=tuning,
            note_count=note_count,
        )
        section = {
            'header': None,
            'events': events,
            'techniques': {},
            'dead_notes': {},
            'barlines': set(barlines),
            'width': total_width,
        }
        parsed_tracks.append((summary, [section]))

    return parsed_tracks


def _parse_gp_legacy_file(file_path):
    """Parse legacy Guitar Pro (.gp3, .gp4, .gp5) files via pyguitarpro if available."""
    try:
        import guitarpro
    except ImportError as e:
        raise ValueError(
            f"Reading legacy {Path(file_path).suffix} files requires either converting to "
            f".gp (GP7/8), .musicxml, or .mid, or installing 'pyguitarpro' (pip install pyguitarpro)."
        ) from e

    song = guitarpro.parse(str(file_path))
    cols_per_measure = 16
    num_measures = max(1, len(song.measureHeaders))
    total_width = num_measures * (cols_per_measure + 1)
    barlines = {m * (cols_per_measure + 1) + cols_per_measure for m in range(num_measures)}

    parsed_tracks = []
    for idx, tr in enumerate(song.tracks, start=1):
        # guitarpro strings are listed 1 (highest) to N (lowest) with MIDI values
        midi_strings = [s.value for s in reversed(tr.strings)]
        tuning = [midi_to_note(m) for m in midi_strings] if midi_strings else ['E2', 'A2', 'D3', 'G3', 'B3', 'E4']
        n_strings = len(tuning)

        if getattr(tr, 'isPercussionTrack', False):
            part_type = 'percussion'
        else:
            semis = parse_tuning(tuning)
            avg_semi = sum(semis) / len(semis)
            part_type = 'bass' if ('bass' in tr.name.lower() or avg_semi < 30) else 'melody'

        events = {}
        note_count = 0
        for m_idx, measure in enumerate(tr.measures):
            for voice in measure.voices[:1]:
                cursor_ticks = 0
                for beat in voice.beats:
                    col_in_m = int(round((cursor_ticks / 3840.0) * cols_per_measure))
                    col_in_m = max(0, min(cols_per_measure - 1, col_in_m))
                    col = m_idx * (cols_per_measure + 1) + col_in_m
                    for note in beat.notes:
                        # note.string is 1-based from highest string
                        s_idx = n_strings - note.string
                        if 0 <= s_idx < n_strings:
                            events[(col, s_idx)] = note.value
                            note_count += 1
                    cursor_ticks += beat.duration.time

        summary = TrackSummary(
            index=idx,
            name=tr.name.strip() or f"Track {idx}",
            part_type=part_type,
            source_tuning=tuning,
            note_count=note_count,
        )
        section = {
            'header': None,
            'events': events,
            'techniques': {},
            'dead_notes': {},
            'barlines': set(barlines),
            'width': total_width,
        }
        parsed_tracks.append((summary, [section]))

    return parsed_tracks


def parse_structured_file(file_path):
    """Parse a structured multi-track file (.mid, .musicxml, .mxl, .gp, .gp3-.gp5).

    Returns:
        List of (TrackSummary, sections_metadata) tuples.
    """
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext in ('.mid', '.midi'):
        return _parse_midi_file(path)
    if ext in ('.musicxml', '.xml', '.mxl'):
        return _parse_musicxml_file(path)
    if ext == '.gp' or (ext in ('.gp3', '.gp4', '.gp5', '.gpx') and zipfile.is_zipfile(path)):
        return _parse_gp_zip_file(path)
    if ext in ('.gp3', '.gp4', '.gp5', '.gpx'):
        return _parse_gp_legacy_file(path)
    raise ValueError(f"Unsupported structured file format: {ext}")


def list_tracks_in_file(file_path):
    """Return a list of TrackSummary objects for a structured file."""
    return [summary for summary, _ in parse_structured_file(file_path)]
