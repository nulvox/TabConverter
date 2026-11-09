"""Command-line interface and high-level orchestration for TabConverter."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from .music import (
    get_melody_strings,
    list_bundled_tunings,
    load_config,
    parse_source_tuning_arg,
    parse_tuning,
)
from .parser import (
    TechniqueInfo,
    extract_sections_with_metadata,
    is_structured_file,
    list_tracks_in_file,
    parse_structured_file,
    parse_tab_file,
)
from .renderer import render_merged_sections
from .solver import align_part_sections, try_octave_shifts


def _filter_structured_tracks(parsed_tracks, track_selector):
    """Filter tracks from a structured file by 1-based index or name substring."""
    if not track_selector:
        # Default: keep all non-percussion tracks with notes
        selected = [
            (summary, secs)
            for summary, secs in parsed_tracks
            if summary.part_type != 'percussion' and summary.note_count > 0
        ]
        return selected or parsed_tracks

    selectors = [s.strip() for s in track_selector.split(',') if s.strip()]
    wanted_indices = set()
    wanted_names = []
    for sel in selectors:
        if sel.isdigit():
            wanted_indices.add(int(sel))
        else:
            wanted_names.append(sel.lower())

    selected = []
    for summary, secs in parsed_tracks:
        if summary.index in wanted_indices or any(w in summary.name.lower() for w in wanted_names):
            selected.append((summary, secs))

    if not selected:
        available = ', '.join(f"{s.index}:{s.name}" for s, _ in parsed_tracks)
        raise ValueError(f"No tracks matched --tracks {track_selector!r} (available: {available})")
    return selected


def merge_tab_files(file_paths, output_path, config=None, source_tunings_list=None,
                    verbosity=0, track_selector=None, wrap_width=None):
    """Convert or merge one or more ASCII/structured tab files into a target tuning tab."""
    target_tuning_list = config.get('target_tuning') if config else None
    if not target_tuning_list:
        print("Error: No target tuning specified in config", file=sys.stderr)
        return 1

    num_target_strings = len(target_tuning_list)
    target_semi = parse_tuning(target_tuning_list)
    melody_strings = get_melody_strings(target_tuning_list, config)
    bass_strings = [i for i in range(num_target_strings) if i not in melody_strings] or list(range(num_target_strings))
    mel_strings_list = sorted(melody_strings) or list(range(num_target_strings))

    all_parts = []  # List of (part_type, source_tuning, sections_metadata) tuples

    for idx, file_path in enumerate(file_paths):
        file_path = Path(file_path)
        if is_structured_file(file_path):
            try:
                parsed_tracks = parse_structured_file(file_path)
                selected_tracks = _filter_structured_tracks(parsed_tracks, track_selector)
            except (OSError, ValueError, Exception) as e:
                print(f"Error reading structured file {file_path}: {e}", file=sys.stderr)
                return 1

            for summary, sections in selected_tracks:
                if verbosity >= 1:
                    print(
                        f"Track {summary.index} ({summary.name}): {summary.part_type} part, "
                        f"tuning {' '.join(summary.source_tuning)}, {summary.note_count} notes"
                    )
                all_parts.append((summary.part_type, summary.source_tuning, sections))
            continue

        try:
            detected_tuning, lines = parse_tab_file(file_path)
        except OSError as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1

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

    single_part_mode = (len(all_parts) == 1)

    section_counts = [len(sections) for _, _, sections in all_parts]
    if len(set(section_counts)) > 1:
        counts = ', '.join(f"part {i + 1}: {n}" for i, n in enumerate(section_counts))
        print(f"Warning: input parts have different section counts ({counts}). "
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

        # column -> [(part_idx, part_type, src_string_idx, note_pitch, fret, TechniqueInfo|None)]
        section_events = defaultdict(list)
        # column -> [(part_idx, part_type, src_string_idx)]
        section_dead_notes = defaultdict(list)
        max_col = 0
        section_header = None
        section_barlines = set()

        for part_idx, sec_meta in slot:
            part_type, source_tuning, _ = all_parts[part_idx]
            header = sec_meta['header']
            section = sec_meta['events']
            tech_map = sec_meta.get('techniques', {})
            dead_map = sec_meta.get('dead_notes', {})
            barlines = sec_meta.get('barlines', set())
            sec_width = sec_meta.get('width', 0)

            if section_header is None and header:
                section_header = header
            if not section_barlines and barlines:
                section_barlines = set(barlines)
            elif section_barlines and barlines and section_barlines != barlines:
                section_barlines &= barlines

            source_semi = parse_tuning(source_tuning)
            max_col = max(max_col, sec_width - 1)

            for (col, src_string_idx), fret in section.items():
                source_pitch = source_semi[src_string_idx]
                note_pitch = source_pitch + fret
                tech = tech_map.get((col, src_string_idx))
                section_events[col].append((part_idx, part_type, src_string_idx, note_pitch, fret, tech))
                max_col = max(max_col, col)

            for (col, src_string_idx) in dead_map:
                section_dead_notes[col].append((part_idx, part_type, src_string_idx))
                max_col = max(max_col, col)

        target_section = defaultdict(dict)  # target_string_idx -> {col: fret}
        target_techniques = {}  # (target_string_idx, col) -> TechniqueInfo

        prev_hand_fret = {'bass': None, 'melody': None}
        prev_pitch_placement = {'bass': {}, 'melody': {}}
        # Map (part_idx, src_string_idx, col) -> placed_target_string_idx for legato/slide chains
        placed_source_notes = {}

        all_cols = sorted(set(section_events.keys()) | set(section_dead_notes.keys()))

        for col in all_cols:
            events = section_events.get(col, [])
            occupied_strings = set()
            bass_frets = {}
            melody_frets = {}

            for pass_type in ['bass', 'melody']:
                pass_events = [e for e in events if e[1] == pass_type]
                pass_events.sort(key=lambda e: e[3])

                for part_idx, part_type, src_string_idx, note_pitch, orig_fret, tech in pass_events:
                    total_notes += 1
                    other_hand_frets = melody_frets if part_type == 'bass' else bass_frets
                    same_hand_frets = bass_frets if part_type == 'bass' else melody_frets

                    prev_str, prev_fr = prev_pitch_placement[part_type].get(
                        note_pitch, (None, prev_hand_fret[part_type])
                    )

                    linked_target_str = None
                    if tech is not None and tech.linked_prev_col is not None:
                        linked_target_str = placed_source_notes.get(
                            (part_idx, src_string_idx, tech.linked_prev_col)
                        )

                    tgt_idx, new_fret = try_octave_shifts(
                        note_pitch, part_type, target_tuning_list,
                        occupied_strings, config, other_hand_frets,
                        prefer_melody_strings=(part_type == 'melody'),
                        prev_fret=prev_fr if prev_fr is not None else prev_hand_fret[part_type],
                        prev_string=prev_str,
                        chord_frets=same_hand_frets,
                        single_part_mode=single_part_mode,
                        linked_target_string=linked_target_str,
                    )

                    if tgt_idx is not None:
                        target_section[tgt_idx][col] = new_fret
                        occupied_strings.add(tgt_idx)
                        placed_source_notes[(part_idx, src_string_idx, col)] = tgt_idx

                        if tech is not None:
                            # Only emit connector ('h', 'p', '/', '\') if the linked previous note
                            # actually landed on the same target string
                            if linked_target_str is not None and tgt_idx != linked_target_str:
                                tech = TechniqueInfo(
                                    bend_semitones=tech.bend_semitones,
                                    release_offset=tech.release_offset,
                                    vibrato=tech.vibrato,
                                    connector_before='',
                                    linked_prev_col=None,
                                )
                            target_techniques[(tgt_idx, col)] = tech

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

            # Place dead/muted notes ('x') on an available string in the appropriate region
            for part_idx, part_type, src_string_idx in section_dead_notes.get(col, []):
                pref_pool = bass_strings if part_type == 'bass' else mel_strings_list
                chosen = None
                if src_string_idx < len(pref_pool) and pref_pool[src_string_idx] not in occupied_strings:
                    chosen = pref_pool[src_string_idx]
                else:
                    for cand in pref_pool + list(range(num_target_strings)):
                        if cand not in occupied_strings:
                            chosen = cand
                            break
                if chosen is not None:
                    target_section[chosen][col] = 'x'
                    target_techniques[(chosen, col)] = TechniqueInfo(is_dead_note=True)
                    occupied_strings.add(chosen)

        merged_sections.append((section_header, target_section, max_col, section_barlines, target_techniques))

    output_text = render_merged_sections(
        merged_sections, target_tuning_list, config, wrap_width=wrap_width
    )

    if output_path is None:
        sys.stdout.write(output_text)
    else:
        try:
            with open(output_path, 'w') as f:
                f.write(output_text)
        except OSError as e:
            print(f"Error writing output file: {e}", file=sys.stderr)
            return 1
        print(f"\nMerged {len(all_parts)} part(s) into {output_path}")
        print(f"Combined {len(merged_sections)} section(s)")
        if verbosity >= 1 and total_notes > 0:
            print(
                f"Notes mapped: {exact_pitch_notes}/{total_notes} exact pitch, "
                f"{octave_shifted_notes} octave-shifted, {unmappable_notes} unplayable"
            )

    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Convert and merge guitar/bass tabs (ASCII, MIDI, MusicXML, Guitar Pro) '
                    'into any target tuning or multi-string instrument tab'
    )

    parser.add_argument('inputs', type=Path, nargs='*',
                        help='Input tab/score file(s) (.tab, .txt, .mid, .musicxml, .mxl, .gp)')
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
                             'If omitted, auto-detected/inferred from the file')
    parser.add_argument('--list-tracks', action='store_true',
                        help='List tracks in structured input file(s) (.mid, .musicxml, .gp) and exit')
    parser.add_argument('--tracks',
                        help='Comma-separated 1-based track indices or name substrings to include '
                             'from structured files (default: all non-percussion tracks)')
    parser.add_argument('--wrap', type=int, default=None, metavar='COLS',
                        help='Wrap output tab lines to at most COLS characters (preferring measure barlines)')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity (-v for summary, -vv for note mapping, -vvv for parser debug)')
    parser.add_argument('--list-tunings', action='store_true',
                        help='List bundled tuning names and exit')

    args = parser.parse_args()

    if args.list_tunings:
        for name in list_bundled_tunings():
            print(name)
        return 0

    if args.list_tracks:
        if not args.inputs:
            parser.error('--list-tracks requires at least one input file')
        for fp in args.inputs:
            try:
                tracks = list_tracks_in_file(fp)
            except Exception as e:
                print(f"Error reading tracks from {fp}: {e}", file=sys.stderr)
                return 1
            for tr in tracks:
                n_strings = len(tr.source_tuning)
                tuning_str = ' '.join(tr.source_tuning)
                print(
                    f"{tr.index}: {tr.name} ({tr.part_type}, {n_strings} strings: {tuning_str}, "
                    f"{tr.note_count} notes)"
                )
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

    return merge_tab_files(
        args.inputs,
        args.output,
        config,
        source_tunings_list,
        args.verbose,
        track_selector=args.tracks,
        wrap_width=args.wrap,
    )
