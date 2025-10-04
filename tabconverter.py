#!/usr/bin/env python3
"""Convert guitar tabs from one tuning to another and merge multiple tab files."""

import argparse
import json
import re
import sys
from pathlib import Path


NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def note_to_semitones(note):
    """Convert note name to semitone offset from C."""
    note = note.upper().strip()
    match = re.match(r'^([A-G][#b]?)(\d+)$', note)
    if not match:
        raise ValueError(f"Invalid note format: {note}")
    
    note_name, octave = match.groups()
    octave = int(octave)
    
    # Handle flats
    if 'b' in note_name:
        note_name = NOTES[(NOTES.index(note_name[0]) - 1) % 12]
    
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
    
    return config['target_tuning']


def detect_source_tuning(tab_lines):
    """Extract source tuning from tab file if present."""
    # Look for tuning in first few lines (format: E|---, A|---, etc.)
    tuning_pattern = re.compile(r'^([A-G][#b]?\d+)\|')
    tuning = []
    
    for line in tab_lines[:20]:  # Check first 20 lines
        match = tuning_pattern.match(line.strip())
        if match:
            tuning.append(match.group(1))
    
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


def convert_fret(fret_str, semitone_diff):
    """Convert a fret number based on semitone difference."""
    if fret_str == '-' or not fret_str.isdigit():
        return fret_str
    
    fret = int(fret_str)
    new_fret = fret + semitone_diff
    
    if new_fret < 0:
        return 'X'  # Unplayable
    
    return str(new_fret)


def convert_tab_line(line, source_tuning, target_tuning, string_idx):
    """Convert a single tab line to new tuning."""
    if string_idx >= len(source_tuning) or string_idx >= len(target_tuning):
        return line  # Can't convert
    
    semitone_diff = target_tuning[string_idx] - source_tuning[string_idx]
    
    # Replace note label
    target_note = semitones_to_note(target_tuning[string_idx])
    # Remove octave number for display
    target_note_display = re.sub(r'\d+', '', target_note)
    
    # Split at pipe
    parts = line.split('|', 1)
    if len(parts) != 2:
        return line
    
    prefix, tab_content = parts
    
    # Convert frets in the tab content
    converted = []
    i = 0
    while i < len(tab_content):
        if tab_content[i].isdigit():
            # Handle multi-digit frets
            num_str = tab_content[i]
            j = i + 1
            while j < len(tab_content) and tab_content[j].isdigit():
                num_str += tab_content[j]
                j += 1
            converted.append(convert_fret(num_str, semitone_diff))
            i = j
        else:
            converted.append(tab_content[i])
            i += 1
    
    return f"{target_note_display}|{''.join(converted)}"


def convert_tabs(lines, source_tuning, target_tuning):
    """Convert all tab lines to new tuning."""
    source_semi = parse_tuning(source_tuning)
    target_semi = parse_tuning(target_tuning)
    
    if len(source_semi) != len(target_semi):
        raise ValueError(f"Source and target tunings must have same number of strings: "
                        f"{len(source_semi)} vs {len(target_semi)}")
    
    tab_pattern = re.compile(r'^([A-G][#b]?\d*)\|')
    converted_lines = []
    string_idx = 0
    
    for line in lines:
        match = tab_pattern.match(line.strip())
        if match:
            converted_line = convert_tab_line(line, source_semi, target_semi, string_idx)
            converted_lines.append(converted_line)
            string_idx += 1
        else:
            converted_lines.append(line)
            # Reset string index when we hit a non-tab line
            if not line.strip() or not any(c in line for c in '|-'):
                string_idx = 0
    
    return converted_lines


def extract_tab_sections(lines):
    """Extract tab sections from lines, grouping consecutive tab lines."""
    tab_pattern = re.compile(r'^([A-G][#b]?\d*)\|')
    sections = []
    current_section = []
    in_tab = False
    
    for line in lines:
        if tab_pattern.match(line.strip()):
            current_section.append(line)
            in_tab = True
        else:
            if in_tab and current_section:
                sections.append(current_section)
                current_section = []
                in_tab = False
    
    if current_section:
        sections.append(current_section)
    
    return sections


def normalize_tab_width(tab_lines):
    """Ensure all tab lines in a section have the same width."""
    if not tab_lines:
        return tab_lines
    
    # Find max width after the pipe
    max_width = 0
    for line in tab_lines:
        parts = line.split('|', 1)
        if len(parts) == 2:
            max_width = max(max_width, len(parts[1]))
    
    # Pad all lines to max width
    normalized = []
    for line in tab_lines:
        parts = line.split('|', 1)
        if len(parts) == 2:
            prefix, content = parts
            padded = content.ljust(max_width, '-')
            normalized.append(f"{prefix}|{padded}")
        else:
            normalized.append(line)
    
    return normalized


def get_string_pitch(tab_line):
    """Extract pitch from a tab line for sorting."""
    tab_pattern = re.compile(r'^([A-G][#b]?\d+)\|')
    match = tab_pattern.match(tab_line.strip())
    if match:
        try:
            return note_to_semitones(match.group(1))
        except:
            return 0
    return 0


def merge_tab_files(file_paths, output_path):
    """Merge multiple tab files into a single combined tab file."""
    all_sections = []
    
    # Parse all input files
    for file_path in file_paths:
        try:
            _, lines, _ = parse_tab_file(file_path)
            sections = extract_tab_sections(lines)
            all_sections.append((file_path.name, sections))
        except Exception as e:
            print(f"Error reading {file_path}: {e}", file=sys.stderr)
            return 1
    
    # Determine number of sections to merge (use minimum)
    min_sections = min(len(sections) for _, sections in all_sections)
    
    if min_sections == 0:
        print("Error: No tab sections found in input files", file=sys.stderr)
        return 1
    
    # Merge sections
    merged_lines = []
    for section_idx in range(min_sections):
        # Add section separator if not first section
        if section_idx > 0:
            merged_lines.append("")
        
        # Collect all tab lines for this section from all files
        section_tabs = []
        for file_name, sections in all_sections:
            if section_idx < len(sections):
                section_tabs.extend(sections[section_idx])
        
        # Sort by pitch (highest to lowest for standard notation)
        section_tabs.sort(key=get_string_pitch, reverse=True)
        
        # Normalize width and add to output
        normalized = normalize_tab_width(section_tabs)
        merged_lines.extend(normalized)
    
    # Write output
    try:
        with open(output_path, 'w') as f:
            f.write('\n'.join(merged_lines) + '\n')
        print(f"Merged {len(file_paths)} files into {output_path}")
        print(f"Combined {min_sections} section(s) with {len(merged_lines)} total lines")
    except Exception as e:
        print(f"Error writing output file: {e}", file=sys.stderr)
        return 1
    
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Convert guitar tabs from one tuning to another and merge multiple tab files'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # Convert command
    convert_parser = subparsers.add_parser('convert', help='Convert tab file to new tuning')
    convert_parser.add_argument('input', type=Path, help='Input tab file')
    convert_parser.add_argument('output', type=Path, help='Output tab file')
    convert_parser.add_argument('-c', '--config', type=Path, required=True,
                               help='Configuration file with target tuning')
    convert_parser.add_argument('-s', '--source-tuning', nargs='+',
                               help='Source tuning (e.g., E2 A2 D3 G3 B3 E4). '
                                    'If not provided, will attempt to detect from file')
    
    # Merge command
    merge_parser = subparsers.add_parser('merge', help='Merge multiple tab files')
    merge_parser.add_argument('inputs', type=Path, nargs='+', 
                             help='Input tab files to merge')
    merge_parser.add_argument('-o', '--output', type=Path, required=True,
                             help='Output merged tab file')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == 'convert':
        # Load target tuning from config
        try:
            target_tuning = load_config(args.config)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
            return 1
        
        # Parse tab file
        try:
            detected_tuning, lines, tab_lines = parse_tab_file(args.input)
        except Exception as e:
            print(f"Error reading input file: {e}", file=sys.stderr)
            return 1
        
        # Determine source tuning
        if args.source_tuning:
            source_tuning = args.source_tuning
        elif detected_tuning:
            source_tuning = detected_tuning
            print(f"Detected source tuning: {' '.join(source_tuning)}")
        else:
            print("Error: Could not detect source tuning. Use -s to specify.", 
                  file=sys.stderr)
            return 1
        
        # Convert tabs
        try:
            converted_lines = convert_tabs(lines, source_tuning, target_tuning)
        except Exception as e:
            print(f"Error converting tabs: {e}", file=sys.stderr)
            return 1
        
        # Write output
        try:
            with open(args.output, 'w') as f:
                f.write('\n'.join(converted_lines) + '\n')
            print(f"Converted tabs written to {args.output}")
        except Exception as e:
            print(f"Error writing output file: {e}", file=sys.stderr)
            return 1
    
    elif args.command == 'merge':
        return merge_tab_files(args.inputs, args.output)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
