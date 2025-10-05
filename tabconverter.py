#!/usr/bin/env python3
"""Convert guitar tabs from one tuning to another and merge multiple tab files."""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict


NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def note_to_semitones(note):
    """Convert note name to semitone offset from C."""
    note = note.strip()
    match = re.match(r'^([A-G][#b]?)(\d+)$', note)
    if not match:
        raise ValueError(f"Invalid note format: {note}")
    
    note_name, octave = match.groups()
    octave = int(octave)
    
    # Handle flats - preserve lowercase b
    if 'b' in note_name:
        base_note = note_name[0].upper()
        note_name = NOTES[(NOTES.index(base_note) - 1) % 12]
    else:
        note_name = note_name.upper()
    
    if note_name not in NOTES:
        raise ValueError(f"Invalid note: {note_name}")
    
    return NOTES.index(note_name) + (octave * 12)


def semitones_to_note(semitones):
    """Convert semitone offset to note name."""
    octave = semitones // 12
    note = NOTES[semitones % 12]
    return f"{note}{octave}"


def parse_tuning(tuning):
    """Parse tuning from list of note strings to semitone offsets."""
    return [note_to_semitones(note) for note in tuning]


def load_config(config_path):
    """Load target tuning from config file."""
    with open(config_path) as f:
        config = json.load(f)
    
    if 'target_tuning' not in config:
        raise ValueError("Config must contain 'target_tuning' key")
    
    return config


def detect_source_tuning(tab_lines):
    """Extract source tuning from tab file if present."""
    # Look for tuning in first few lines (format: E|---, A|---, etc.)
    tuning_pattern = re.compile(r'^([A-G][#b]?\d+)\|')
    tuning = []
    seen = set()
    
    for line in tab_lines[:50]:  # Check first 50 lines
        match = tuning_pattern.match(line.strip())
        if match:
            note = match.group(1)
            if note not in seen:
                tuning.append(note)
                seen.add(note)
    
    if tuning:
        return tuning
    
    return None


def parse_tab_file(file_path):
    """Parse tab file and extract tuning and tab lines."""
    with open(file_path) as f:
        lines = [line.rstrip('\n') for line in f]
    
    source_tuning = detect_source_tuning(lines)
    
    # Find tab sections (lines with pipe and dashes/numbers)
    tab_pattern = re.compile(r'[A-G][#b]?\d*\|[\d\-hpbr/\\~\|]+')
    tab_lines = []
    
    for line in lines:
        if tab_pattern.match(line.strip()):
            tab_lines.append(line)
    
    return source_tuning, lines, tab_lines


def extract_note_events(lines, source_tuning, verbosity=0):
    """Extract note events with their temporal positions (column indices).
    
    Returns: List of sections, where each section is a dict mapping
             (column_idx, source_string_idx) -> fret_number
    """
    source_semi = parse_tuning(source_tuning)
    tab_pattern = re.compile(r'^([A-G][#b]?\d*)\|')
    
    # First, identify all tab line groups (sections)
    sections_raw = []
    current_section_lines = []
    
    if verbosity >= 3:
        print(f"  Scanning {len(lines)} lines for tab sections...")
    
    for line in lines:
        match = tab_pattern.match(line.strip())
        if match:
            current_section_lines.append(line)
            if verbosity >= 3 and len(current_section_lines) <= 2:
                print(f"    Found tab line: {line[:60]}...")
        else:
            # Non-tab line - end current section
            if current_section_lines:
                if verbosity >= 3:
                    print(f"    Section ended with {len(current_section_lines)} lines")
                sections_raw.append(current_section_lines)
                current_section_lines = []
    
    if current_section_lines:
        if verbosity >= 3:
            print(f"    Final section with {len(current_section_lines)} lines")
        sections_raw.append(current_section_lines)
    
    # Now parse each section
    sections = []
    
    for section_lines in sections_raw:
        section_events = {}
        
        for line in section_lines:
            parts = line.split('|', 1)
            if len(parts) != 2:
                continue
            
            note_label, content = parts
            note_label = note_label.strip()
            
            # Find which source string this is by matching the full note label
            string_idx = None
            for i, tuning_note in enumerate(source_tuning):
                # Match either with or without octave number
                if note_label == tuning_note or note_label == re.sub(r'\d+', '', tuning_note):
                    string_idx = i
                    break
            
            if string_idx is None:
                if verbosity >= 3:
                    print(f"    Warning: Could not match note label '{note_label}' to tuning {source_tuning}")
                continue
            
            # Extract notes at each column position
            i = 0
            while i < len(content):
                if content[i].isdigit():
                    # Handle multi-digit frets
                    num_str = content[i]
                    j = i + 1
                    while j < len(content) and content[j].isdigit():
                        num_str += content[j]
                        j += 1
                    
                    fret = int(num_str)
                    # Only accept reasonable fret numbers (0-24)
                    if fret <= 24:
                        section_events[(i, string_idx)] = fret
                    i = j
                else:
                    i += 1
        
        if section_events:
            sections.append(section_events)
    
    return sections


def find_best_target_string(note_pitch, part_type, target_tuning, occupied_strings, 
                            config, other_hand_frets=None, prefer_melody_strings=True):
    """Find the best target string for a note considering all constraints.
    
    Args:
        note_pitch: Absolute pitch of the note in semitones
        part_type: 'bass' or 'melody'
        target_tuning: List of target tuning notes
        occupied_strings: Set of string indices already used at this timestamp
        config: Config dict with fret constraints
        other_hand_frets: Dict of {string_idx: fret} for the other hand at this timestamp
        prefer_melody_strings: If True, prefer melody strings for melody parts
    
    Returns:
        (target_string_idx, fret) or (None, None) if impossible
    """
    target_semi = parse_tuning(target_tuning)
    num_strings = len(target_semi)
    melody_start = num_strings // 2
    
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
    
    candidates = []
    
    # Try all target strings
    for tgt_idx in range(num_strings):
        if tgt_idx in occupied_strings:
            continue
        
        tgt_pitch = target_semi[tgt_idx]
        fret = note_pitch - tgt_pitch
        
        # Check basic fret range
        if fret < 0 or fret > max_fret:
            continue
        
        # Check hand-specific fret range
        if fret < fret_min or fret > fret_max:
            continue
        
        # Check hand separation constraint
        if other_hand_frets:
            collision = False
            for other_string, other_fret in other_hand_frets.items():
                # Check if frets are too close (within hand_separation)
                if abs(fret - other_fret) < hand_separation:
                    collision = True
                    break
            if collision:
                continue
        
        # Calculate preference score (lower is better)
        is_melody_string = tgt_idx >= melody_start
        
        # Strong preference for staying in the appropriate string region
        if prefer_low_strings and not is_melody_string:
            region_penalty = 0  # Bass strongly prefers bass strings
        elif prefer_low_strings and is_melody_string:
            region_penalty = 100  # Bass should avoid melody strings (heavy penalty)
        elif not prefer_low_strings and is_melody_string:
            region_penalty = 0  # Melody strongly prefers melody strings
        elif not prefer_low_strings and not is_melody_string:
            region_penalty = 100  # Melody should avoid bass strings (heavy penalty)
        else:
            region_penalty = 0
        
        # Prefer frets in the middle of the allowed range (easier to play)
        fret_center = (fret_min + fret_max) / 2
        fret_penalty = abs(fret - fret_center) * 0.1
        
        score = region_penalty + fret_penalty
        candidates.append((score, tgt_idx, fret))
    
    if not candidates:
        return None, None
    
    # Return best candidate
    candidates.sort()
    _, best_idx, best_fret = candidates[0]
    return best_idx, best_fret


def try_octave_shifts(note_pitch, part_type, target_tuning, occupied_strings, 
                     config, other_hand_frets=None, prefer_melody_strings=True):
    """Try finding a playable position by shifting octaves.
    
    Tries in this order:
    1. Current octave in preferred string region
    2. ±1 octave in preferred string region  
    3. ±2 octaves in preferred string region
    4. Current octave in any region (fallback)
    5. ±1 octave in any region (fallback)
    6. ±2 octaves in any region (fallback)
    
    Returns:
        (target_string_idx, fret) or (None, None) if impossible
    """
    target_semi = parse_tuning(target_tuning)
    melody_start = len(target_semi) // 2
    prefer_low = (part_type == 'bass')
    
    # Try current octave in preferred region first
    result = find_best_target_string(note_pitch, part_type, target_tuning, 
                                     occupied_strings, config, other_hand_frets, 
                                     prefer_melody_strings)
    if result[0] is not None:
        # Check if we stayed in preferred region
        is_melody_string = result[0] >= melody_start
        
        # Accept if in preferred region
        if (prefer_low and not is_melody_string) or (not prefer_low and is_melody_string):
            return result
    
    # Try octave shifts in preferred region before accepting wrong region
    for octave_shift in [12, -12, 24, -24]:
        shifted_result = find_best_target_string(note_pitch + octave_shift, part_type, 
                                                 target_tuning, occupied_strings, config, 
                                                 other_hand_frets, prefer_melody_strings)
        if shifted_result[0] is not None:
            # Check if in preferred region
            is_melody_string = shifted_result[0] >= melody_start
            if (prefer_low and not is_melody_string) or (not prefer_low and is_melody_string):
                return shifted_result
    
    # If we got here, nothing worked in preferred region
    # Accept the original result even if wrong region, or try more octaves as last resort
    if result[0] is not None:
        return result
    
    # Last resort: try all octave shifts even in wrong region
    for octave_shift in [12, -12, 24, -24]:
        shifted_result = find_best_target_string(note_pitch + octave_shift, part_type, 
                                                 target_tuning, occupied_strings, config, 
                                                 other_hand_frets, prefer_melody_strings)
        if shifted_result[0] is not None:
            return shifted_result
    
    return None, None


def merge_tab_files(file_paths, output_path, config=None, source_tunings_list=None, verbosity=0):
    """Merge multiple tab files into a single combined tab file."""
    
    target_tuning_list = config.get('target_tuning') if config else None
    if not target_tuning_list:
        print("Error: No target tuning specified in config", file=sys.stderr)
        return 1
    
    num_target_strings = len(target_tuning_list)
    
    # Parse all input files and extract note events
    all_parts = []  # List of (part_type, sections) tuples
    
    for idx, file_path in enumerate(file_paths):
        try:
            detected_tuning, lines, _ = parse_tab_file(file_path)
        except Exception as e:
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
            print(f"Error: Could not detect source tuning for {file_path}. Use -s to specify.", 
                  file=sys.stderr)
            return 1
        
        # Determine part type (bass or melody)
        source_semi = parse_tuning(source_tuning)
        avg_pitch = sum(source_semi) / len(source_semi)
        part_type = 'bass' if avg_pitch < 30 else 'melody'
        
        if verbosity >= 1:
            print(f"{file_path.name}: {part_type} part (avg pitch {avg_pitch:.1f})")
        
        # Extract note events
        sections = extract_note_events(lines, source_tuning, verbosity)
        all_parts.append((part_type, source_tuning, sections))
        
        if verbosity >= 1:
            print(f"  Found {len(sections)} sections")
    
    # Now merge sections temporally
    # We need to align sections across parts - assume they correspond by index
    max_sections = max(len(sections) for _, _, sections in all_parts)
    
    merged_sections = []
    
    for section_idx in range(max_sections):
        if verbosity >= 2:
            print(f"\nProcessing section {section_idx + 1}/{max_sections}")
        
        # Collect all note events for this section across all parts
        section_events = defaultdict(list)  # column -> [(part_type, source_tuning, string_idx, fret)]
        max_col = 0
        
        for part_type, source_tuning, sections in all_parts:
            if section_idx >= len(sections):
                continue
            
            section = sections[section_idx]
            source_semi = parse_tuning(source_tuning)
            
            for (col, src_string_idx), fret in section.items():
                source_pitch = source_semi[src_string_idx]
                note_pitch = source_pitch + fret
                section_events[col].append((part_type, note_pitch, fret))
                max_col = max(max_col, col)
        
        # Allocate notes to target strings column by column
        target_section = defaultdict(dict)  # target_string_idx -> {col: fret}
        
        for col in sorted(section_events.keys()):
            events = section_events[col]
            occupied_strings = set()
            bass_frets = {}  # For checking hand separation
            melody_frets = {}
            
            # Process in two passes: bass first, then melody
            # This ensures bass gets priority for low strings
            for pass_type in ['bass', 'melody']:
                for part_type, note_pitch, orig_fret in events:
                    if part_type != pass_type:
                        continue
                    
                    # Get other hand's frets for separation check
                    other_hand_frets = melody_frets if part_type == 'bass' else bass_frets
                    
                    # Find best target string with octave shifting
                    tgt_idx, new_fret = try_octave_shifts(
                        note_pitch, part_type, target_tuning_list, 
                        occupied_strings, config, other_hand_frets,
                        prefer_melody_strings=(part_type == 'melody')
                    )
                    
                    if tgt_idx is not None:
                        target_section[tgt_idx][col] = new_fret
                        occupied_strings.add(tgt_idx)
                        
                        if part_type == 'bass':
                            bass_frets[tgt_idx] = new_fret
                        else:
                            melody_frets[tgt_idx] = new_fret
                        
                        if verbosity >= 2:
                            print(f"  Col {col}: {part_type} note (pitch {note_pitch}) -> string {tgt_idx} fret {new_fret}")
                    else:
                        if verbosity >= 2:
                            print(f"  Col {col}: {part_type} note (pitch {note_pitch}) -> UNMAPPABLE (X)")
                        # Still need to mark it somehow - we'll use a special marker
                        # Find any available string and mark with 'X'
                        for tgt_idx in range(num_target_strings):
                            if tgt_idx not in occupied_strings:
                                target_section[tgt_idx][col] = 'X'
                                occupied_strings.add(tgt_idx)
                                break
        
        merged_sections.append((target_section, max_col))
    
    # Write output
    merged_lines = []
    target_displays = [re.sub(r'\d+', '', note) for note in target_tuning_list]
    max_label_width = max(len(d) for d in target_displays)
    
    for section_idx, (target_section, max_col) in enumerate(merged_sections):
        if section_idx > 0:
            merged_lines.append("")
        
        # First pass: determine width needed at each column position
        col_widths = {}
        for col in range(max_col + 1):
            max_width = 1  # At least one dash
            for tgt_idx in range(num_target_strings):
                if tgt_idx in target_section and col in target_section[tgt_idx]:
                    fret = target_section[tgt_idx][col]
                    if fret == 'X':
                        width = 1
                    else:
                        width = len(str(fret))
                    max_width = max(max_width, width)
            col_widths[col] = max_width
        
        # Build tab lines for each target string (high to low)
        for tgt_idx in reversed(range(num_target_strings)):
            label = target_displays[tgt_idx].ljust(max_label_width)
            
            # Build content string with proper spacing
            content = []
            for col in range(max_col + 1):
                col_width = col_widths[col]
                
                if tgt_idx in target_section and col in target_section[tgt_idx]:
                    fret = target_section[tgt_idx][col]
                    if fret == 'X':
                        content.append('X'.ljust(col_width, '-'))
                    else:
                        fret_str = str(fret)
                        content.append(fret_str.ljust(col_width, '-'))
                else:
                    content.append('-' * col_width)
            
            merged_lines.append(f"{label}|{''.join(content)}")
    
    # Write output file
    try:
        with open(output_path, 'w') as f:
            f.write('\n'.join(merged_lines) + '\n')
        print(f"\nMerged {len(file_paths)} files into {output_path}")
        print(f"Combined {len(merged_sections)} section(s)")
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        return 1
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Convert guitar tabs from one tuning to another and merge multiple tab files'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Merge command (primary use case)
    merge_parser = subparsers.add_parser('merge', help='Merge multiple tab files')
    merge_parser.add_argument('inputs', type=Path, nargs='+', 
                             help='Input tab files to merge')
    merge_parser.add_argument('-o', '--output', type=Path, required=True,
                             help='Output merged tab file')
    merge_parser.add_argument('-c', '--config', type=Path, required=True,
                             help='Configuration file with target tuning')
    merge_parser.add_argument('-s', '--source-tuning', action='append',
                             help='Source tuning for input file as comma-separated notes (e.g., "E1,A1,D2,G2"). '
                                  'Use multiple -s flags in order of input files. '
                                  'If not provided, will attempt to detect from files')
    merge_parser.add_argument('-v', '--verbose', action='count', default=0,
                             help='Increase verbosity (-v for basic, -vv for detailed mapping, -vvv for all debug)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == 'merge':
        try:
            config = load_config(args.config)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return 1
        
        # Parse source tunings if provided (comma-separated format)
        source_tunings_list = None
        if hasattr(args, 'source_tuning') and args.source_tuning:
            source_tunings_list = []
            for tuning_str in args.source_tuning:
                tuning = [note.strip() for note in tuning_str.split(',')]
                source_tunings_list.append(tuning)
        
        return merge_tab_files(args.inputs, args.output, config, source_tunings_list, args.verbose)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())